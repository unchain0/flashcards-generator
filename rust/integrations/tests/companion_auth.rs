use axum::{
    Json, Router,
    body::Body,
    http::{StatusCode, header},
    response::Response,
    routing::post,
};
use flashcards_integrations::companion_auth::{CompanionAuthError, RemoteTokenVerifier};
use flashcards_services::authentication::CompanionTokenVerifier;
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};

async fn verification_response(Json(request): Json<Value>) -> Result<Response, StatusCode> {
    let token = request["access_token"]
        .as_str()
        .ok_or(StatusCode::BAD_REQUEST)?;
    if token == "oversized-chunked" {
        return Response::builder()
            .status(StatusCode::CREATED)
            .body(Body::from_stream(futures_util::stream::iter([
                Ok::<_, std::convert::Infallible>("a".repeat(4096)),
                Ok("a".repeat(4097)),
            ])))
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR);
    }
    if token == "redirect" {
        return Response::builder()
            .status(StatusCode::TEMPORARY_REDIRECT)
            .header(header::LOCATION, "/redirect-target")
            .body(Body::empty())
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR);
    }
    if token == "slow" {
        tokio::time::sleep(Duration::from_secs(6)).await;
    }
    let (status, body) = match token {
        "valid" | "slow" => (
            StatusCode::CREATED,
            json!({"subject":"a".repeat(32)}).to_string(),
        ),
        "expired" => (StatusCode::UNAUTHORIZED, String::new()),
        "wrong-status" => (
            StatusCode::OK,
            json!({"subject":"a".repeat(32)}).to_string(),
        ),
        "invalid-subject" => (
            StatusCode::CREATED,
            json!({"subject":"../profile"}).to_string(),
        ),
        "invalid-json" => (StatusCode::CREATED, "invalid".to_owned()),
        "extra-fields" => (
            StatusCode::CREATED,
            json!({"subject":"a".repeat(32), "unexpected":true}).to_string(),
        ),
        "oversized" => (StatusCode::CREATED, "a".repeat(8193)),
        _ => panic!("unexpected request"),
    };
    Response::builder()
        .status(status)
        .body(Body::from(body))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

#[tokio::test]
async fn validates_remote_protocol_and_bounds_untrusted_responses() {
    let redirects = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route("/api/v1/auth/companion/verify", post(verification_response))
        .route(
            "/redirect-target",
            post(
                |axum::extract::State(hits): axum::extract::State<Arc<AtomicUsize>>| async move {
                    hits.fetch_add(1, Ordering::SeqCst);
                    StatusCode::INTERNAL_SERVER_ERROR
                },
            ),
        )
        .with_state(redirects.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let verifier = RemoteTokenVerifier::new(&origin).unwrap();
    assert_eq!(
        verifier.verify("valid").await.unwrap().unwrap().as_str(),
        "a".repeat(32)
    );
    assert!(verifier.verify("expired").await.unwrap().is_none());
    assert!(verifier.verify("").await.unwrap().is_none());
    assert!(verifier.verify(&"a".repeat(4097)).await.unwrap().is_none());
    assert!(matches!(
        verifier.verify("wrong-status").await,
        Err(CompanionAuthError::UnexpectedStatus(StatusCode::OK))
    ));
    assert!(matches!(
        verifier.verify("invalid-subject").await,
        Err(CompanionAuthError::Identity(_))
    ));
    for token in ["invalid-json", "extra-fields"] {
        assert!(matches!(
            verifier.verify(token).await,
            Err(CompanionAuthError::Json(_))
        ));
    }
    assert!(matches!(
        verifier.verify("oversized").await,
        Err(CompanionAuthError::OversizedResponse)
    ));
    let chunked = verifier.verify("oversized-chunked").await;
    assert!(
        matches!(chunked, Err(CompanionAuthError::OversizedResponse)),
        "{chunked:?}"
    );
    assert!(matches!(
        verifier.verify("redirect").await,
        Err(CompanionAuthError::UnexpectedStatus(
            StatusCode::TEMPORARY_REDIRECT
        ))
    ));
    assert_eq!(redirects.load(Ordering::SeqCst), 0);
    assert!(
        matches!(verifier.verify("slow").await, Err(CompanionAuthError::Http(error)) if error.is_timeout())
    );
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
    assert!(matches!(
        verifier.verify("valid").await,
        Err(CompanionAuthError::Http(_))
    ));
}
