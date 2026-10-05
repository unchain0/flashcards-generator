use axum::{
    Json, Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
    routing::post,
};
use flashcards_delivery::companion::{CompanionState, router};
use flashcards_integrations::{
    companion_auth::RemoteTokenVerifier, notebooklm_browser::NotebookLMBrowserLogin,
    notebooklm_profiles::LocalNotebookLMProfiles,
};
use serde_json::{Value, json};
use tower::ServiceExt;

#[cfg(unix)]
#[tokio::test]
async fn unsafe_profile_ancestors_report_provider_error_without_requesting_google_login() {
    let hosted = Router::new().route("/api/v1/auth/companion/verify", post(verify_token));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, hosted).await.unwrap() });
    let temporary = tempfile::tempdir().unwrap();
    let outside = temporary.path().join("outside");
    std::fs::create_dir(&outside).unwrap();
    std::fs::write(outside.join("original"), "preserve external data").unwrap();
    let alias = temporary.path().join("alias");
    std::os::unix::fs::symlink(&outside, &alias).unwrap();
    let app = router(CompanionState::new(
        RemoteTokenVerifier::new(&origin).unwrap(),
        LocalNotebookLMProfiles::new(alias.join("companion")).unwrap(),
        NotebookLMBrowserLogin::new(Some(temporary.path().join("missing-browser"))),
        flashcards_integrations::local_job_store::LocalJobStore::new().unwrap(),
    ))
    .unwrap();
    for (path, method, message) in [
        (
            "/v1/notebooklm/status",
            "GET",
            "unable to check authentication",
        ),
        ("/v1/notebooklm/login", "POST", "unable to complete login"),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(path)
                    .header("origin", &origin)
                    .header("authorization", "Bearer valid-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
        assert_eq!(
            body,
            json!({"authenticated":false,"status":"provider_error","message":message})
        );
    }
    assert_eq!(std::fs::read_dir(&outside).unwrap().count(), 1);
    assert_eq!(
        std::fs::read_to_string(outside.join("original")).unwrap(),
        "preserve external data"
    );
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

#[tokio::test]
async fn guards_local_profiles_and_preserves_browser_status_and_cors_contracts() {
    let hosted = Router::new().route("/api/v1/auth/companion/verify", post(verify_token));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, hosted).await.unwrap() });
    let temporary = tempfile::tempdir().unwrap();
    let data_dir = temporary.path().join("companion");
    let app = router(CompanionState::new(
        RemoteTokenVerifier::new(&origin).unwrap(),
        LocalNotebookLMProfiles::new(data_dir.clone()).unwrap(),
        NotebookLMBrowserLogin::new(Some(temporary.path().join("missing-browser"))),
        flashcards_integrations::local_job_store::LocalJobStore::new().unwrap(),
    ))
    .unwrap();

    check_status_authorization(&app, &origin, &data_dir).await;
    check_cors_preflight(&app, &origin, &data_dir).await;
    check_login_authorization(&app, &origin, &data_dir).await;
    check_profile_status_and_login(&app, &origin, &data_dir).await;
    check_unreadable_profile_status(&app, &origin, &data_dir).await;
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn check_unreadable_profile_status(app: &Router, origin: &str, data_dir: &std::path::Path) {
    let config = data_dir.join("profiles/0123456789abcdef0123456789abcdef/config.json");
    std::fs::create_dir(&config).unwrap();
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/v1/notebooklm/status")
                .header("origin", origin)
                .header("authorization", "Bearer valid-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    assert_eq!(
        body,
        json!({"authenticated":false,"status":"provider_error","message":"unable to check authentication"})
    );
    assert!(config.is_dir());
}

async fn verify_token(Json(body): Json<Value>) -> (StatusCode, Json<Value>) {
    if body["access_token"] == "valid-token" {
        (
            StatusCode::CREATED,
            Json(json!({"subject":"0123456789abcdef0123456789abcdef"})),
        )
    } else {
        (StatusCode::UNAUTHORIZED, Json(json!({"detail":"expired"})))
    }
}

async fn check_status_authorization(app: &Router, origin: &str, data_dir: &std::path::Path) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/v1/health")
                .header("origin", origin)
                .header("origin", "https://attacker.example")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(response.headers()["cache-control"], "no-store");
    assert_eq!(response.headers()["x-content-type-options"], "nosniff");
    assert!(!data_dir.exists());
    for (path, request_origin, token, expected) in [
        ("/v1/health", None, None, StatusCode::UNAUTHORIZED),
        (
            "/v1/health",
            Some("https://attacker.example"),
            None,
            StatusCode::UNAUTHORIZED,
        ),
        ("/v1/health", Some(origin), None, StatusCode::OK),
        (
            "/v1/notebooklm/status",
            Some(origin),
            None,
            StatusCode::UNAUTHORIZED,
        ),
        (
            "/v1/notebooklm/status",
            Some(origin),
            Some("expired"),
            StatusCode::UNAUTHORIZED,
        ),
        (
            "/v1/notebooklm/status",
            Some("https://attacker.example"),
            Some("valid-token"),
            StatusCode::UNAUTHORIZED,
        ),
    ] {
        let mut request = Request::builder().uri(path);
        if let Some(origin) = request_origin {
            request = request.header("origin", origin);
        }
        if let Some(token) = token {
            request = request.header("authorization", format!("Bearer {token}"));
        }
        let response = app
            .clone()
            .oneshot(request.body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(response.status(), expected);
        assert_eq!(response.headers()["cache-control"], "no-store");
        assert_eq!(response.headers()["x-content-type-options"], "nosniff");
        assert!(
            !data_dir.exists(),
            "Unauthorized requests must not access local profiles"
        );
    }
}

async fn check_cors_preflight(app: &Router, origin: &str, data_dir: &std::path::Path) {
    for request_origin in [origin, "https://attacker.example"] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/v1/notebooklm/status")
                    .header("origin", request_origin)
                    .header("access-control-request-method", "GET")
                    .header("access-control-request-headers", "authorization")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(
            response
                .headers()
                .get("access-control-allow-origin")
                .is_some(),
            request_origin == origin
        );
        assert!(
            !response
                .headers()
                .contains_key("access-control-allow-credentials")
        );
        assert!(!data_dir.exists());
    }
}

async fn check_login_authorization(app: &Router, origin: &str, data_dir: &std::path::Path) {
    for (request_origin, token) in [
        (origin, "expired"),
        ("https://attacker.example", "valid-token"),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/notebooklm/login")
                    .header("origin", request_origin)
                    .header("authorization", format!("Bearer {token}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert!(!data_dir.exists());
    }
}

async fn check_profile_status_and_login(app: &Router, origin: &str, data_dir: &std::path::Path) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/v1/notebooklm/status")
                .header("origin", origin)
                .header("authorization", "Bearer valid-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["access-control-allow-origin"], origin);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    assert_eq!(
        body,
        json!({"authenticated":false,"status":"login_required","message":"login required"})
    );
    assert!(
        data_dir
            .join("profiles/0123456789abcdef0123456789abcdef")
            .is_dir()
    );
    let storage = data_dir
        .join("profiles/0123456789abcdef0123456789abcdef/profiles/default/storage_state.json");
    let original = r#"{"cookies":[{"value":"private-cookie-value"}]}"#;
    std::fs::write(&storage, original).unwrap();
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/v1/notebooklm/status")
                .header("origin", origin)
                .header("authorization", "Bearer valid-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    assert_eq!(
        body,
        json!({"authenticated":false,"status":"login_required","message":"login required"})
    );
    assert_eq!(std::fs::read_to_string(&storage).unwrap(), original);
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/notebooklm/login")
                .header("origin", origin)
                .header("authorization", "Bearer valid-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    assert_eq!(
        body,
        json!({"authenticated":false,"status":"login_required","message":"Chrome or Chromium is required for NotebookLM login"})
    );
    assert_eq!(std::fs::read_to_string(storage).unwrap(), original);
}
