use super::*;
use axum::body::to_bytes;
use flashcards_integrations_server::auth_crypto::AuthCrypto;

#[tokio::test]
async fn refuses_cookie_header_injection_without_exposing_session_tokens() {
    let url =
        std::env::var("FLASHCARDS_TEST_DATABASE_URL").expect("disposable PostgreSQL URL required");
    let crypto = AuthCrypto::new(
        "cookie-test-session-secret-0123456789",
        "cookie-test-lookup-secret-0123456789",
    )
    .unwrap();
    let token = crypto.new_session_token().unwrap();
    let database = WebDatabase::connect(&url, crypto).await.unwrap();
    let state = WebState::new(database.clone(), true, 3600);
    for private in [
        "private-session\r\nSet-Cookie: injected=1",
        "private-session\0injected",
    ] {
        let error = auth_response(&state, true, private, 3600)
            .expect_err("Unsafe session token must not become a header");
        let response = error.into_response();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert!(!response.headers().contains_key(header::SET_COOKIE));
        let body: serde_json::Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
        assert!(body["detail"].is_string());
        assert_eq!(body.as_object().unwrap().len(), 1);
        assert!(!body.to_string().contains("private-session"));
        assert!(!body.to_string().contains("injected"));
    }
    let response = auth_response(&state, true, &token, 3600)
        .unwrap_or_else(|_| panic!("Generated session token must remain usable"));
    let cookie = response.headers()[header::SET_COOKIE].to_str().unwrap();
    assert!(cookie.starts_with(&format!("flashcards_session={token};")));
    for flag in [
        "HttpOnly",
        "Max-Age=3600",
        "Path=/",
        "SameSite=Lax",
        "Secure",
    ] {
        assert!(cookie.contains(flag));
    }
    database.close().await;
}
