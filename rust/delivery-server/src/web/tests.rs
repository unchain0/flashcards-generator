use super::*;
use axum::body::to_bytes;
use axum::http::{HeaderValue, StatusCode, header};
use axum_test::TestServer;
use flashcards_integrations_server::auth_crypto::AuthCrypto;
use std::path::{Path, PathBuf};

fn static_dir() -> PathBuf {
    // The frontend is built at the workspace root level
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../src/flashcards_generator/delivery/web/static/dist")
}

#[tokio::test]
async fn security_headers_are_present_in_development() {
    let static_dir = static_dir();
    assert!(static_dir.exists(), "Static dir not found: {static_dir:?}");
    assert!(
        static_dir.join("index.html").is_file(),
        "index.html not found: {:?}",
        static_dir.join("index.html")
    );

    let state = WebState::new(
        WebDatabase::connect(
            &std::env::var("FLASHCARDS_TEST_DATABASE_URL")
                .expect("disposable PostgreSQL URL required"),
            AuthCrypto::new(
                "header-test-session-secret-0123456789",
                "header-test-lookup-secret-0123456789",
            )
            .unwrap(),
        )
        .await
        .unwrap(),
        false,
        3600,
    );
    let app = router(state, &static_dir, vec![]).expect("router should build");
    let server = TestServer::new(app);
    let response = server.get("/health/live").await;

    assert_eq!(response.status_code(), StatusCode::OK);

    // Content-Security-Policy
    let csp = response.header("content-security-policy");
    assert!(csp.to_str().unwrap().contains("default-src 'self'"));
    assert!(csp.to_str().unwrap().contains("script-src 'self'"));
    assert!(csp.to_str().unwrap().contains("style-src 'self'"));
    assert!(csp.to_str().unwrap().contains("img-src 'self'"));
    assert!(
        csp.to_str()
            .unwrap()
            .contains("connect-src 'self' http://127.0.0.1:8766")
    );
    assert!(
        csp.to_str()
            .unwrap()
            .contains("https://o4505598204248064.ingest.us.sentry.io")
    );
    assert!(csp.to_str().unwrap().contains("object-src 'none'"));
    assert!(csp.to_str().unwrap().contains("base-uri 'none'"));
    assert!(csp.to_str().unwrap().contains("frame-ancestors 'none'"));
    assert!(csp.to_str().unwrap().contains("form-action 'self'"));

    // Referrer-Policy
    assert_eq!(
        response.header("referrer-policy"),
        HeaderValue::from_static("no-referrer")
    );

    // X-Content-Type-Options
    assert_eq!(
        response.header("x-content-type-options"),
        HeaderValue::from_static("nosniff")
    );

    // X-Frame-Options
    assert_eq!(
        response.header("x-frame-options"),
        HeaderValue::from_static("DENY")
    );

    // Permissions-Policy
    let permissions = response.header("permissions-policy");
    let perm_str = permissions.to_str().unwrap();
    assert!(perm_str.contains("camera=()"));
    assert!(perm_str.contains("geolocation=()"));
    assert!(perm_str.contains("microphone=()"));

    // Cache-Control
    assert_eq!(
        response.header("cache-control"),
        HeaderValue::from_static("no-store")
    );

    // HSTS should NOT be present in development
    assert!(
        response
            .headers()
            .get("strict-transport-security")
            .is_none()
    );
}

#[tokio::test]
async fn security_headers_include_hsts_in_production() {
    let static_dir = static_dir();
    assert!(static_dir.exists(), "Static dir not found: {static_dir:?}");
    assert!(
        static_dir.join("index.html").is_file(),
        "index.html not found: {:?}",
        static_dir.join("index.html")
    );

    let state = WebState::new(
        WebDatabase::connect(
            &std::env::var("FLASHCARDS_TEST_DATABASE_URL")
                .expect("disposable PostgreSQL URL required"),
            AuthCrypto::new(
                "header-test-session-secret-0123456789",
                "header-test-lookup-secret-0123456789",
            )
            .unwrap(),
        )
        .await
        .unwrap(),
        true,
        3600,
    );
    let app = router(state, &static_dir, vec![]).expect("router should build");
    let server = TestServer::new(app);
    let response = server.get("/health/live").await;

    assert_eq!(response.status_code(), StatusCode::OK);

    // HSTS should be present in production
    assert_eq!(
        response.header("strict-transport-security"),
        HeaderValue::from_static("max-age=31536000")
    );

    // Other headers should also be present
    assert!(response.headers().get("content-security-policy").is_some());
    assert_eq!(
        response.header("referrer-policy"),
        HeaderValue::from_static("no-referrer")
    );
    assert_eq!(
        response.header("x-content-type-options"),
        HeaderValue::from_static("nosniff")
    );
    assert_eq!(
        response.header("x-frame-options"),
        HeaderValue::from_static("DENY")
    );
    assert!(response.headers().get("permissions-policy").is_some());
    assert_eq!(
        response.header("cache-control"),
        HeaderValue::from_static("no-store")
    );
}

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
