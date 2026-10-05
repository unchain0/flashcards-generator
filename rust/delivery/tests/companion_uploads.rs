use axum::{
    Json, Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
    routing::post,
};
use flashcards_delivery::companion::{CompanionState, router};
use flashcards_integrations::{
    companion_auth::RemoteTokenVerifier, local_job_store::LocalJobStore,
    notebooklm_browser::NotebookLMBrowserLogin, notebooklm_profiles::LocalNotebookLMProfiles,
};
use serde_json::{Value, json};
use std::{
    io,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tower::ServiceExt;

#[tokio::test]
async fn rejects_unauthorized_or_disconnected_uploads_without_reading_documents() {
    let hosted = Router::new().route("/api/v1/auth/companion/verify", post(verify_token));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, hosted).await.unwrap() });
    let directory = tempfile::tempdir().unwrap();
    let profiles = directory.path().join("profiles");
    let jobs = LocalJobStore::new().unwrap();
    let app = router(CompanionState::new(
        RemoteTokenVerifier::new(&origin).unwrap(),
        LocalNotebookLMProfiles::new(profiles.clone()).unwrap(),
        NotebookLMBrowserLogin::new(None),
        jobs.clone(),
    ))
    .unwrap();
    for (request_origin, token, expected) in [
        (origin.as_str(), None, StatusCode::UNAUTHORIZED),
        (
            "https://attacker.example",
            Some("owner-token"),
            StatusCode::UNAUTHORIZED,
        ),
        (
            origin.as_str(),
            Some("expired-token"),
            StatusCode::UNAUTHORIZED,
        ),
        (
            origin.as_str(),
            Some("invalid-identity"),
            StatusCode::UNAUTHORIZED,
        ),
        (
            origin.as_str(),
            Some("invalid-response"),
            StatusCode::SERVICE_UNAVAILABLE,
        ),
        (
            origin.as_str(),
            Some("unavailable-server"),
            StatusCode::SERVICE_UNAVAILABLE,
        ),
        (origin.as_str(), Some("owner-token"), StatusCode::CONFLICT),
    ] {
        reject_request(&app, request_origin, token, expected, &profiles, &jobs)
            .await
            .unwrap();
    }
    jobs.close().unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn verify_token(Json(body): Json<Value>) -> (StatusCode, Json<Value>) {
    match body["access_token"].as_str() {
        Some("owner-token") => (StatusCode::CREATED, Json(json!({"subject":"a".repeat(32)}))),
        Some("invalid-identity") => (
            StatusCode::CREATED,
            Json(json!({"subject":"../private-profile"})),
        ),
        Some("invalid-response") => (
            StatusCode::CREATED,
            Json(json!({"private-field":"private-token"})),
        ),
        Some("unavailable-server") => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail":"private-server-error"})),
        ),
        _ => (StatusCode::UNAUTHORIZED, Json(json!({"detail":"expired"}))),
    }
}

async fn reject_request(
    app: &Router,
    request_origin: &str,
    token: Option<&str>,
    expected: StatusCode,
    profiles: &std::path::Path,
    jobs: &LocalJobStore,
) -> Result<(), Box<dyn std::error::Error>> {
    let reads = Arc::new(AtomicUsize::new(0));
    let count = reads.clone();
    let body = Body::from_stream(futures_util::stream::once(async move {
        count.fetch_add(1, Ordering::SeqCst);
        Ok::<_, io::Error>(b"private source document".as_slice())
    }));
    let mut request = Request::post("/v1/jobs")
        .header("origin", request_origin)
        .header("content-type", "multipart/form-data; boundary=private-name");
    if let Some(token) = token {
        request = request.header("authorization", format!("Bearer {token}"));
    }
    let response = tokio::time::timeout(
        Duration::from_secs(5),
        app.clone().oneshot(request.body(body)?),
    )
    .await??;
    assert_eq!(response.status(), expected);
    assert_eq!(reads.load(Ordering::SeqCst), 0);
    assert_eq!(response.headers()["cache-control"], "no-store");
    assert_eq!(response.headers()["x-content-type-options"], "nosniff");
    let value: Value = serde_json::from_slice(&to_bytes(response.into_body(), 8192).await?)?;
    assert!(!value.to_string().contains("private"));
    if expected == StatusCode::CONFLICT {
        assert_eq!(
            value,
            json!({"detail":"Conecte sua conta do NotebookLM antes de gerar flashcards."})
        );
    } else {
        assert!(!profiles.exists());
    }
    assert!(jobs.start_next()?.is_none());
    Ok(())
}
