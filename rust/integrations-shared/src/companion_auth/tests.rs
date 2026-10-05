use super::*;
use axum::{Json, Router, extract::State, http::header, response::IntoResponse, routing::post};
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::TcpListener,
};

#[test]
fn normalizes_exact_origins_and_rejects_remote_plaintext_and_url_credentials() {
    for (input, expected) in [
        (" HTTPS://Example.COM:443/ ", "https://example.com"),
        ("http://LOCALHOST:80/", "http://localhost"),
        ("http://127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("http://[::1]:8080/", "http://[::1]:8080"),
    ] {
        assert_eq!(normalized_origin(input).unwrap(), expected);
    }
    for invalid in [
        "",
        "https://",
        "http://example.com",
        "ftp://localhost",
        "https://user@example.com",
        "https://@example.com",
        "https://example.com/path",
        "https://example.com/path/..",
        "https://example.com//",
        "https://example.com?x=1",
        "https://example.com#x",
        "https://example.com:invalid",
        "https://example.com\\",
    ] {
        assert!(normalized_origin(invalid).is_err(), "{invalid}");
    }
}

#[tokio::test]
async fn invalid_tokens_never_reach_the_server_and_verified_identities_remain_opaque() {
    let contacted = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route("/api/v1/auth/companion/verify", post(verify_opaque_token))
        .with_state(contacted.clone());
    let (verifier, server) = start_verifier(app).await;
    for invalid in [String::new(), "a".repeat(4097)] {
        assert!(verifier.verify(&invalid).await.unwrap().is_none());
    }
    assert_eq!(contacted.load(Ordering::SeqCst), 0);
    assert_eq!(
        verifier
            .verify("synthetic-accepted-token")
            .await
            .unwrap()
            .unwrap()
            .as_str(),
        "0123456789abcdef0123456789abcdef"
    );
    assert!(
        verifier
            .verify("synthetic-revoked-token")
            .await
            .unwrap()
            .is_none()
    );
    assert_eq!(contacted.load(Ordering::SeqCst), 2);
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn verify_opaque_token(
    State(contacted): State<Arc<AtomicUsize>>,
    Json(request): Json<Value>,
) -> (StatusCode, Json<Value>) {
    contacted.fetch_add(1, Ordering::SeqCst);
    assert_eq!(request.as_object().unwrap().len(), 1);
    if request["access_token"] == "synthetic-accepted-token" {
        return (
            StatusCode::CREATED,
            Json(json!({"subject":"0123456789abcdef0123456789abcdef"})),
        );
    }
    (StatusCode::UNAUTHORIZED, Json(json!({})))
}

async fn start_verifier(app: Router) -> (RemoteTokenVerifier, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let verifier =
        RemoteTokenVerifier::new(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (verifier, server)
}

#[tokio::test]
async fn rejects_invalid_remote_identities_and_redirects_without_disclosing_private_payloads() {
    for (status, body) in [
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            "private-server-error".to_owned(),
        ),
        (StatusCode::CREATED, "private-invalid-json".to_owned()),
        (
            StatusCode::CREATED,
            json!({"subject":"private-invalid-identity"}).to_string(),
        ),
        (
            StatusCode::CREATED,
            json!({"subject":"0123456789abcdef0123456789abcdef","email":"private-email"})
                .to_string(),
        ),
        (StatusCode::CREATED, "private-large-response".repeat(512)),
        (StatusCode::TEMPORARY_REDIRECT, String::new()),
    ] {
        check_verification_response(status, body).await;
    }
}

#[derive(Clone)]
struct VerificationFixture {
    status: StatusCode,
    body: String,
    contacted: Arc<AtomicUsize>,
    redirected: Arc<AtomicUsize>,
}

async fn fixture_verification(
    State(fixture): State<VerificationFixture>,
    Json(request): Json<Value>,
) -> impl IntoResponse {
    fixture.contacted.fetch_add(1, Ordering::SeqCst);
    assert_eq!(request["access_token"], "private-synthetic-token");
    (
        fixture.status,
        [(header::LOCATION, "/redirect-destination")],
        fixture.body,
    )
}

async fn fixture_redirect(State(fixture): State<VerificationFixture>) -> impl IntoResponse {
    fixture.redirected.fetch_add(1, Ordering::SeqCst);
    (
        StatusCode::CREATED,
        Json(json!({"subject":"0123456789abcdef0123456789abcdef"})),
    )
}

async fn check_verification_response(status: StatusCode, body: String) {
    let fixture = VerificationFixture {
        status,
        body,
        contacted: Arc::new(AtomicUsize::new(0)),
        redirected: Arc::new(AtomicUsize::new(0)),
    };
    let app = Router::new()
        .route("/api/v1/auth/companion/verify", post(fixture_verification))
        .route("/redirect-destination", post(fixture_redirect))
        .with_state(fixture.clone());
    let (verifier, server) = start_verifier(app).await;
    let error = verifier
        .verify("private-synthetic-token")
        .await
        .unwrap_err();
    assert!(!error.to_string().contains("private-"));
    match &error {
        CompanionAuthError::Identity(cause) => {
            assert!(error.source().is_some());
            assert!(!cause.to_string().contains("private-"));
        }
        CompanionAuthError::Json(_) => assert!(error.source().is_some()),
        CompanionAuthError::OversizedResponse | CompanionAuthError::UnexpectedStatus(_) => {
            assert!(error.source().is_none());
        }
        _ => panic!("Unexpected verifier error: {error:?}"),
    }
    assert_eq!(fixture.contacted.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.redirected.load(Ordering::SeqCst), 0);
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

#[tokio::test]
async fn truncated_identity_responses_preserve_the_transport_cause_without_exposing_tokens() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let verifier =
        RemoteTokenVerifier::new(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let server = tokio::spawn(serve_truncated_identity(listener));
    let error = verifier
        .verify("private-synthetic-token")
        .await
        .unwrap_err();
    let CompanionAuthError::Http(cause) = &error else {
        panic!("Truncated HTTP bodies must retain their transport failure");
    };
    assert!(cause.is_decode());
    assert!(
        cause
            .source()
            .unwrap()
            .downcast_ref::<reqwest::Error>()
            .unwrap()
            .is_body()
    );
    assert!(error.source().is_some());
    assert!(!error.to_string().contains("private-"));
    server.await.unwrap();
}

async fn serve_truncated_identity(listener: TcpListener) {
    let (socket, _) = listener.accept().await.unwrap();
    let mut reader = BufReader::new(socket);
    let mut length = None;
    let mut header_bytes = 0;
    loop {
        let mut line = String::new();
        assert!(reader.read_line(&mut line).await.unwrap() > 0);
        header_bytes += line.len();
        assert!(header_bytes <= 8192);
        if line == "\r\n" {
            break;
        }
        if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
            length = Some(value.trim().parse::<usize>().unwrap());
        }
    }
    let length = length.unwrap();
    assert!(length < 1024);
    let mut body = vec![0; length];
    reader.read_exact(&mut body).await.unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap()["access_token"],
        "private-synthetic-token"
    );
    let mut socket = reader.into_inner();
    socket
        .write_all(b"HTTP/1.1 201 Created\r\nContent-Length: 200\r\nConnection: close\r\n\r\n{\"subject\":\"private-truncated")
        .await
        .unwrap();
    socket.shutdown().await.unwrap();
}
