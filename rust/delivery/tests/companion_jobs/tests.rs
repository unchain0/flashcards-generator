use axum::{
    Json, Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
    routing::post,
};
use flashcards_delivery::companion::{CompanionState, router};
use flashcards_domain::identity::UserId;
use flashcards_integrations::{
    companion_auth::RemoteTokenVerifier, local_job_store::LocalJobStore,
    notebooklm_browser::NotebookLMBrowserLogin, notebooklm_profiles::LocalNotebookLMProfiles,
};
use flashcards_services::{
    document_inputs::safe_filenames,
    generation_options::GenerationOptions,
    local_jobs::{JobStatus, SourceOutcome},
};
use serde_json::{Value, json};
use std::{
    fs,
    io::Write,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};
use tower::ServiceExt;

#[tokio::test]
async fn polls_owned_jobs_and_streams_registered_artifacts_with_revocable_access() {
    let revoked = Arc::new(AtomicBool::new(false));
    let hosted = Router::new()
        .route("/api/v1/auth/companion/verify", post(verify_token))
        .with_state(revoked.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, hosted).await.unwrap() });
    let temporary = tempfile::tempdir().unwrap();
    let profile_dir = temporary.path().join("profiles");
    let jobs = LocalJobStore::new().unwrap();
    let id = submit_job(&jobs);
    let app = router(CompanionState::new(
        RemoteTokenVerifier::new(&origin).unwrap(),
        LocalNotebookLMProfiles::new(profile_dir.clone()).unwrap(),
        NotebookLMBrowserLogin::new(None),
        jobs.clone(),
    ))
    .unwrap();
    let poll = format!("/v1/jobs/{id}");
    check_queued(&app, &origin, &poll).await;
    let (path, csv) = complete_job(&jobs);
    let download = check_snapshot(&app, &origin, &poll, &id).await;
    check_access(&app, &origin, &poll, &download).await;
    check_download(&app, &origin, &download, &path, csv).await;
    check_invalid_and_revoked(&app, &origin, &id, &poll, &download, &revoked).await;
    #[cfg(unix)]
    check_external_artifact(&app, &origin, &download, &path, &temporary).await;
    jobs.close().unwrap();
    let response = app
        .oneshot(request(&poll, &origin, Some("owner-token")))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(
        !profile_dir.exists(),
        "Job routes must not touch Google profiles"
    );
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

fn request(path: &str, origin: &str, token: Option<&str>) -> Request<Body> {
    let mut request = Request::builder().uri(path).header("origin", origin);
    if let Some(token) = token {
        request = request.header("authorization", format!("Bearer {token}"));
    }
    request.body(Body::empty()).unwrap()
}

async fn verify_token(
    axum::extract::State(revoked): axum::extract::State<Arc<AtomicBool>>,
    Json(body): Json<Value>,
) -> (StatusCode, Json<Value>) {
    let subject = match body["access_token"].as_str() {
        Some("owner-token") if !revoked.load(Ordering::SeqCst) => Some("a".repeat(32)),
        Some("other-token") => Some("b".repeat(32)),
        _ => None,
    };
    match subject {
        Some(subject) => (StatusCode::CREATED, Json(json!({"subject":subject}))),
        None => (StatusCode::UNAUTHORIZED, Json(json!({"detail":"expired"}))),
    }
}

fn submit_job(jobs: &LocalJobStore) -> String {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let filenames = safe_filenames(&["study.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(
            owner.clone(),
            filenames.clone(),
            GenerationOptions::default(),
        )
        .unwrap();
    reservation
        .create_input(&filenames[0])
        .unwrap()
        .write_all(b"private source document")
        .unwrap();
    let id = reservation.id().to_owned();
    reservation.submit().unwrap();
    id
}

async fn check_queued(app: &Router, origin: &str, poll: &str) {
    let response = app
        .clone()
        .oneshot(request(poll, origin, Some("owner-token")))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let queued: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    assert_eq!(queued["status"], "queued");
    assert_eq!(queued["filenames"], json!(["01-study.pdf"]));
}

fn complete_job(jobs: &LocalJobStore) -> (std::path::PathBuf, &'static str) {
    let execution = jobs.start_next().unwrap().unwrap();
    let artifact = "parts/ação +#%\".csv";
    let csv = "\"{{c1::café}}\",\"Português\"\r\n";
    let path = {
        let mut workspace = execution.workspace().unwrap();
        fs::create_dir(workspace.output_dir().join("parts")).unwrap();
        let path = workspace.output_dir().join(artifact);
        fs::write(&path, csv).unwrap();
        workspace.register_artifact(artifact).unwrap();
        path
    };
    jobs.record_source(&execution, SourceOutcome::Completed)
        .unwrap();
    jobs.finish(&execution, JobStatus::Completed).unwrap();
    drop(execution);
    (path, csv)
}

async fn check_snapshot(app: &Router, origin: &str, poll: &str, id: &str) -> String {
    let response = app
        .clone()
        .oneshot(request(poll, origin, Some("owner-token")))
        .await
        .unwrap();
    let snapshot: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    let download = format!("/v1/jobs/{id}/artifacts/parts/a%C3%A7%C3%A3o%20%2B%23%25%22.csv");
    assert_eq!(
        snapshot,
        json!({
            "id":id,"status":"completed","message":"Geração concluída.","filenames":["01-study.pdf"],
            "discovered_sources":1,"completed_sources":1,"skipped_sources":0,"failed_sources":0,
            "artifacts":[{"name":"ação +#%\".csv","url":download}],"error":null,
        })
    );
    download
}

async fn check_access(app: &Router, origin: &str, poll: &str, download: &str) {
    for target in [poll, download] {
        check_denied_access(app, origin, target).await;
    }
}

async fn check_download(
    app: &Router,
    origin: &str,
    download: &str,
    path: &std::path::Path,
    csv: &str,
) {
    let response = app
        .clone()
        .oneshot(request(download, origin, Some("owner-token")))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers()["content-type"],
        "text/csv; charset=utf-8"
    );
    assert_eq!(response.headers()["content-length"], csv.len().to_string());
    assert_eq!(
        response.headers()["content-disposition"],
        "attachment; filename=\"flashcards.csv\"; filename*=UTF-8''a%C3%A7%C3%A3o%20%2B%23%25%22.csv"
    );
    assert_eq!(response.headers()["access-control-allow-origin"], origin);
    std::fs::OpenOptions::new()
        .append(true)
        .open(path)
        .unwrap()
        .write_all(b"private data appended after validation")
        .unwrap();
    assert_eq!(to_bytes(response.into_body(), 8192).await.unwrap(), csv);
    fs::write(path, csv).unwrap();
}

async fn check_invalid_and_revoked(
    app: &Router,
    origin: &str,
    id: &str,
    poll: &str,
    download: &str,
    revoked: &AtomicBool,
) {
    for invalid in [
        "/v1/jobs/invalid/artifacts/deck.csv".to_owned(),
        format!("/v1/jobs/{id}/artifacts/missing.csv"),
        format!("/v1/jobs/{id}/artifacts/%2E%2E/secret.csv"),
        "/v1/jobs/invalid".into(),
    ] {
        let response = app
            .clone()
            .oneshot(request(&invalid, origin, Some("owner-token")))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
    revoked.store(true, Ordering::SeqCst);
    for target in [poll, download] {
        let response = app
            .clone()
            .oneshot(request(target, origin, Some("owner-token")))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }
    revoked.store(false, Ordering::SeqCst);
}

#[cfg(unix)]
async fn check_external_artifact(
    app: &Router,
    origin: &str,
    download: &str,
    path: &std::path::Path,
    temporary: &tempfile::TempDir,
) {
    #[cfg(unix)]
    {
        let original = temporary.path().join("original.csv");
        fs::write(&original, "private external data").unwrap();
        fs::remove_file(path).unwrap();
        std::os::unix::fs::symlink(&original, path).unwrap();
        let response = app
            .clone()
            .oneshot(request(download, origin, Some("owner-token")))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            serde_json::from_slice::<Value>(&to_bytes(response.into_body(), 8192).await.unwrap())
                .unwrap(),
            json!({"detail":"Falha local ao acessar geração."})
        );
        assert_eq!(
            fs::read_to_string(original).unwrap(),
            "private external data"
        );
    }
}

async fn check_denied_access(app: &Router, origin: &str, target: &str) {
    for (request_origin, token, status) in [
        (origin, None, StatusCode::UNAUTHORIZED),
        (
            "https://attacker.example",
            Some("owner-token"),
            StatusCode::UNAUTHORIZED,
        ),
        (origin, Some("expired-token"), StatusCode::UNAUTHORIZED),
        (origin, Some("other-token"), StatusCode::NOT_FOUND),
    ] {
        let response = app
            .clone()
            .oneshot(request(target, request_origin, token))
            .await
            .unwrap();
        assert_eq!(response.status(), status);
        assert_eq!(response.headers()["cache-control"], "no-store");
        assert_eq!(response.headers()["x-content-type-options"], "nosniff");
        let body = to_bytes(response.into_body(), 8192).await.unwrap();
        assert!(!String::from_utf8_lossy(&body).contains("private"));
    }
}
