use super::*;
use axum::{
    Router,
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    routing::post,
};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

#[tokio::test]
async fn rejects_invalid_origins_and_credentials_before_remote_verification() {
    let verifier = RemoteTokenVerifier::new("http://127.0.0.1:1").unwrap();
    let mut headers = HeaderMap::new();
    assert!(matches!(
        authorized_user(&headers, &verifier).await,
        Err(AuthorizationError::Origin)
    ));
    headers.insert(header::ORIGIN, "http://127.0.0.1:1".parse().unwrap());
    for invalid in [
        "",
        "Basic token",
        "Bearer",
        "Bearer ",
        "Bearer  token",
        "Bearer token extra",
        "Bearer token\t",
    ] {
        headers.insert(header::AUTHORIZATION, invalid.parse().unwrap());
        assert!(
            matches!(
                authorized_user(&headers, &verifier).await,
                Err(AuthorizationError::Credentials)
            ),
            "{invalid}"
        );
    }
    headers.insert(header::AUTHORIZATION, "Bearer token".parse().unwrap());
    headers.append(header::AUTHORIZATION, "Bearer other".parse().unwrap());
    assert!(matches!(
        authorized_user(&headers, &verifier).await,
        Err(AuthorizationError::Credentials)
    ));
    headers.append(header::ORIGIN, "https://attacker.example".parse().unwrap());
    assert!(matches!(
        authorized_user(&headers, &verifier).await,
        Err(AuthorizationError::Origin)
    ));
    assert_eq!(bearer_token("bEaReR token"), Some("token"));
    for (name, expected_origin) in [(header::ORIGIN, true), (header::AUTHORIZATION, false)] {
        let mut headers = HeaderMap::new();
        headers.insert(header::ORIGIN, verifier.origin().parse().unwrap());
        headers.insert(header::AUTHORIZATION, "Bearer token".parse().unwrap());
        headers.insert(name, axum::http::HeaderValue::from_bytes(b"\xff").unwrap());
        let error = authorized_user(&headers, &verifier).await.unwrap_err();
        assert_eq!(matches!(error, AuthorizationError::Origin), expected_origin);
        assert!(matches!(
            error,
            AuthorizationError::Origin | AuthorizationError::Credentials
        ));
    }
}

#[tokio::test]
async fn propagates_remote_failures_without_accepting_invalid_identities_or_exposing_capabilities()
{
    let requests = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route("/api/v1/auth/companion/verify", post(verification_response))
        .with_state(requests.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let verifier = RemoteTokenVerifier::new(&origin).unwrap();
    let mut headers = HeaderMap::new();
    headers.insert(header::ORIGIN, origin.parse().unwrap());
    headers.insert(
        header::AUTHORIZATION,
        "bEaReR private-capability".parse().unwrap(),
    );
    assert_eq!(
        authorized_user(&headers, &verifier).await.unwrap().as_str(),
        "a".repeat(32)
    );
    for case in 1..6 {
        let error = authorized_user(&headers, &verifier).await.unwrap_err();
        check_remote_failure(case, &error);
        assert!(!error.to_string().contains("private-"));
        assert!(!error.to_string().contains(&origin));
    }
    assert_eq!(requests.load(Ordering::SeqCst), 6);
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
    let error = authorized_user(&headers, &verifier).await.unwrap_err();
    assert!(matches!(
        error,
        AuthorizationError::Unavailable(CompanionAuthError::Http(_))
    ));
    assert!(error.source().unwrap().source().is_some());
    assert!(!error.to_string().contains("private-capability"));
    assert!(!error.to_string().contains(&origin));
}

async fn verification_response(
    State(received): State<Arc<AtomicUsize>>,
    request: Request,
) -> (StatusCode, &'static str) {
    assert!(!request.headers().contains_key(header::COOKIE));
    assert!(!request.headers().contains_key(header::AUTHORIZATION));
    assert_eq!(
        to_bytes(request.into_body(), 8192).await.unwrap().as_ref(),
        br#"{"access_token":"private-capability"}"#
    );
    match received.fetch_add(1, Ordering::SeqCst) {
        0 => (
            StatusCode::CREATED,
            r#"{"subject":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}"#,
        ),
        1 => (StatusCode::UNAUTHORIZED, ""),
        2 => (StatusCode::CREATED, r#"{"subject":"../private-profile"}"#),
        3 => (StatusCode::CREATED, "private-capability"),
        4 => (
            StatusCode::CREATED,
            r#"{"subject":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","email":"private-capability"}"#,
        ),
        5 => (StatusCode::SERVICE_UNAVAILABLE, "private-capability"),
        _ => panic!("unexpected verification request"),
    }
}

fn check_remote_failure(case: usize, error: &AuthorizationError) {
    match case {
        1 | 2 => {
            assert!(matches!(error, AuthorizationError::Expired));
            assert!(error.source().is_none());
        }
        3 | 4 => {
            assert!(matches!(
                error,
                AuthorizationError::Unavailable(CompanionAuthError::Json(_))
            ));
            assert!(error.source().unwrap().source().is_some());
        }
        5 => {
            assert!(matches!(
                error,
                AuthorizationError::Unavailable(CompanionAuthError::UnexpectedStatus(
                    StatusCode::SERVICE_UNAVAILABLE
                ))
            ));
            assert!(error.source().unwrap().source().is_none());
        }
        _ => unreachable!(),
    }
}
