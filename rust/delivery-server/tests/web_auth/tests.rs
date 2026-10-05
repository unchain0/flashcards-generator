use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
    response::Response,
};
use flashcards_delivery_server::companion_auth::{AuthorizationError, authorized_user};
use flashcards_delivery_server::web::{self, WebState};
use flashcards_integrations_server::{auth_crypto::AuthCrypto, web_database::WebDatabase};
use flashcards_integrations_shared::companion_auth::RemoteTokenVerifier;
use serde_json::{Value, json};
use sqlx::{
    PgPool,
    postgres::{PgConnectOptions, PgPoolOptions},
};
use std::{path::Path, str::FromStr};
use tower::ServiceExt;

async fn request(
    app: &Router,
    method: &str,
    path: &str,
    cookie: Option<&str>,
    body: Value,
) -> Response {
    let mut builder = Request::builder().method(method).uri(path);
    if let Some(cookie) = cookie {
        builder = builder.header(header::COOKIE, cookie);
    }
    let body = if body.is_null() {
        Body::empty()
    } else {
        builder = builder.header(header::CONTENT_TYPE, "application/json");
        Body::from(body.to_string())
    };
    app.clone()
        .oneshot(builder.body(body).unwrap())
        .await
        .unwrap()
}

async fn json_body(response: Response) -> Value {
    serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap()
}

#[tokio::test]
async fn browser_authentication_contract_runs_against_postgres() {
    let url =
        std::env::var("FLASHCARDS_TEST_DATABASE_URL").expect("disposable PostgreSQL URL required");
    let admin = PgPool::connect(&url).await.unwrap();
    sqlx::query("CREATE DATABASE flashcards_web")
        .execute(&admin)
        .await
        .unwrap();
    let options = PgConnectOptions::from_str(&url)
        .unwrap()
        .database("flashcards_web");
    let pool = PgPoolOptions::new().connect_with(options).await.unwrap();
    let web_url = format!("{}/flashcards_web", url.rsplit_once('/').unwrap().0);
    let crypto = AuthCrypto::new(
        "web-test-session-secret-0123456789",
        "web-test-lookup-secret-0123456789",
    )
    .unwrap();
    let database = WebDatabase::connect(&web_url, crypto).await.unwrap();
    database.migrate().await.unwrap();
    let user_id = database.create_user("senha-web-de-teste").await.unwrap();
    let static_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../src/flashcards_generator/delivery/web/static/dist");
    assert!(
        web::router(
            WebState::new(database.clone(), true, 3600),
            &static_dir.join("missing"),
            vec![]
        )
        .is_err()
    );
    let app = web::router(
        WebState::new(database.clone(), true, 3600),
        &static_dir,
        vec![],
    )
    .unwrap();
    check_health(&app).await;
    check_invalid_authentication_payloads(&app).await;
    let cookie = check_login(&app).await;
    check_cookie_headers(&app, &cookie).await;
    check_companion_lifecycle(&app, &cookie, &user_id).await;
    check_degraded_health(&app, &database, &static_dir, &pool).await;
    database.close().await;
    pool.close().await;
    admin.close().await;
}

async fn check_health(app: &Router) {
    let live = request(app, "GET", "/health/live", None, Value::Null).await;
    assert_eq!(live.status(), StatusCode::OK);
    assert_eq!(
        live.headers()["strict-transport-security"],
        "max-age=31536000"
    );
    assert_eq!(live.headers()["x-frame-options"], "DENY");
    assert_eq!(live.headers()["cache-control"], "no-store");
    assert_eq!(json_body(live).await, json!({"status":"ok"}));
    assert_eq!(
        request(app, "GET", "/health/ready", None, Value::Null)
            .await
            .status(),
        StatusCode::OK
    );
    let index = request(app, "GET", "/", None, Value::Null).await;
    assert_eq!(index.status(), StatusCode::OK);
    assert!(
        index.headers()[header::CONTENT_TYPE]
            .to_str()
            .unwrap()
            .starts_with("text/html")
    );
    assert_eq!(
        request(app, "GET", "/api/v1/auth/me", None, Value::Null)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
}

async fn check_invalid_authentication_payloads(app: &Router) {
    for body in [
        json!({}),
        json!([]),
        json!({"password":null}),
        json!({"password":"senha-web-de-teste", "email":"private@example.test"}),
        json!({"password":"a".repeat(8192)}),
    ] {
        let response = request(app, "POST", "/api/v1/auth/login", None, body).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert!(!response.headers().contains_key(header::SET_COOKIE));
        assert_eq!(json_body(response).await.as_object().unwrap().len(), 1);
    }
    for body in [
        json!({}),
        json!({"access_token":null}),
        json!({"access_token":3}),
        json!({"access_token":""}),
        json!({"access_token":"a".repeat(4097)}),
        json!({"access_token":"private-token", "subject":"private-user"}),
    ] {
        let response = request(app, "POST", "/api/v1/auth/companion/verify", None, body).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let error = json_body(response).await;
        assert!(error["detail"].is_string());
        assert_eq!(error.as_object().unwrap().len(), 1);
        assert!(!error.to_string().contains("private-"));
    }
}

async fn check_cookie_headers(app: &Router, cookie: &str) {
    use axum::http::HeaderValue;
    for (headers, expected) in [
        (
            vec![
                HeaderValue::from_str(cookie).unwrap(),
                HeaderValue::from_str(cookie).unwrap(),
            ],
            StatusCode::UNAUTHORIZED,
        ),
        (
            vec![HeaderValue::from_bytes(b"unrelated=\xff").unwrap()],
            StatusCode::UNAUTHORIZED,
        ),
        (
            vec![HeaderValue::from_static(
                "unrelated=value; flashcards_session",
            )],
            StatusCode::UNAUTHORIZED,
        ),
        (
            vec![
                HeaderValue::from_str(&format!("unrelated=value; {cookie}; another=with=equals"))
                    .unwrap(),
            ],
            StatusCode::OK,
        ),
        (
            vec![HeaderValue::from_str(&format!("{cookie}=extra")).unwrap()],
            StatusCode::UNAUTHORIZED,
        ),
    ] {
        let mut request = Request::builder()
            .uri("/api/v1/auth/me")
            .body(Body::empty())
            .unwrap();
        for header in headers {
            request.headers_mut().append(header::COOKIE, header);
        }
        let response = app.clone().oneshot(request).await.unwrap();
        assert_eq!(response.status(), expected);
    }
}

async fn check_login(app: &Router) -> String {
    assert_eq!(
        request(
            app,
            "POST",
            "/api/v1/auth/login",
            None,
            json!({"password":3})
        )
        .await
        .status(),
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        request(
            app,
            "POST",
            "/api/v1/auth/login",
            None,
            json!({"password":"incorrect"})
        )
        .await
        .status(),
        StatusCode::UNAUTHORIZED
    );
    let login = request(
        app,
        "POST",
        "/api/v1/auth/login",
        None,
        json!({"password":"senha-web-de-teste"}),
    )
    .await;
    assert_eq!(login.status(), StatusCode::OK);
    let set_cookie = login.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .to_owned();
    for flag in [
        "HttpOnly",
        "SameSite=Lax",
        "Secure",
        "Max-Age=3600",
        "Path=/",
    ] {
        assert!(set_cookie.contains(flag));
    }
    let cookie = set_cookie.split(';').next().unwrap();
    assert_eq!(json_body(login).await, json!({"authenticated":true}));
    assert_eq!(
        json_body(request(app, "GET", "/api/v1/auth/me", Some(cookie), Value::Null).await).await,
        json!({"authenticated":true})
    );
    let duplicate = format!("{cookie}; {cookie}");
    assert_eq!(
        request(app, "GET", "/api/v1/auth/me", Some(&duplicate), Value::Null)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
    cookie.to_owned()
}

async fn check_companion_lifecycle(app: &Router, cookie: &str, user_id: &str) {
    let issued = json_body(
        request(
            app,
            "POST",
            "/api/v1/auth/companion/token",
            Some(cookie),
            Value::Null,
        )
        .await,
    )
    .await;
    assert_eq!(issued["expires_in"], 300);
    let capability = issued["access_token"].as_str().unwrap();
    let VerifiedCompanion {
        verifier,
        companion_headers,
        server,
    } = verify_companion_capability(app, capability, user_id).await;
    let logout = request(
        app,
        "POST",
        "/api/v1/auth/logout",
        Some(cookie),
        Value::Null,
    )
    .await;
    assert_eq!(logout.status(), StatusCode::OK);
    assert!(
        logout.headers()[header::SET_COOKIE]
            .to_str()
            .unwrap()
            .contains("Max-Age=0")
    );
    assert_eq!(json_body(logout).await, json!({"authenticated":false}));
    assert!(matches!(
        authorized_user(&companion_headers, &verifier).await,
        Err(AuthorizationError::Expired)
    ));
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
    check_revoked_sessions(app, cookie, capability).await;
}

async fn check_degraded_health(
    app: &Router,
    database: &WebDatabase,
    static_dir: &Path,
    pool: &PgPool,
) {
    let cookie = check_login(app).await;
    let issued = json_body(
        request(
            app,
            "POST",
            "/api/v1/auth/companion/token",
            Some(&cookie),
            Value::Null,
        )
        .await,
    )
    .await;
    let capability = issued["access_token"].as_str().unwrap();
    let development = web::router(
        WebState::new(database.clone(), false, 3600),
        static_dir,
        vec![],
    )
    .unwrap();
    assert!(
        !request(&development, "GET", "/health/live", None, Value::Null)
            .await
            .headers()
            .contains_key("strict-transport-security")
    );
    sqlx::query("DROP TABLE web_sessions")
        .execute(pool)
        .await
        .unwrap();
    assert_eq!(
        request(app, "GET", "/health/ready", None, Value::Null)
            .await
            .status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    check_database_failure_responses(app, &cookie, capability).await;
}

async fn check_database_failure_responses(app: &Router, cookie: &str, capability: &str) {
    for (method, path, body) in [
        (
            "POST",
            "/api/v1/auth/login",
            json!({"password":"senha-web-de-teste"}),
        ),
        ("POST", "/api/v1/auth/logout", Value::Null),
        ("GET", "/api/v1/auth/me", Value::Null),
        ("POST", "/api/v1/auth/companion/token", Value::Null),
        (
            "POST",
            "/api/v1/auth/companion/verify",
            json!({"access_token":capability}),
        ),
    ] {
        let response = request(app, method, path, Some(cookie), body).await;
        assert_eq!(
            response.status(),
            StatusCode::INTERNAL_SERVER_ERROR,
            "{path}"
        );
        assert!(!response.headers().contains_key(header::SET_COOKIE));
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
        assert_eq!(response.headers()["x-frame-options"], "DENY");
        let error = json_body(response).await;
        assert!(error["detail"].is_string());
        assert_eq!(error.as_object().unwrap().len(), 1);
        for private in [
            "senha-web-de-teste",
            cookie,
            capability,
            "web_sessions",
            "SELECT",
            "INSERT",
        ] {
            assert!(!error.to_string().contains(private));
        }
    }
    assert_eq!(
        request(app, "GET", "/health/live", None, Value::Null)
            .await
            .status(),
        StatusCode::OK
    );
    assert_eq!(
        request(app, "GET", "/", None, Value::Null).await.status(),
        StatusCode::OK
    );
}

struct VerifiedCompanion {
    verifier: RemoteTokenVerifier,
    companion_headers: axum::http::HeaderMap,
    server: tokio::task::JoinHandle<()>,
}

async fn verify_companion_capability(
    app: &Router,
    capability: &str,
    user_id: &str,
) -> VerifiedCompanion {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let http_app = app.clone();
    let server = tokio::spawn(async move { axum::serve(listener, http_app).await.unwrap() });
    let verifier = RemoteTokenVerifier::new(&origin).unwrap();
    let mut companion_headers = axum::http::HeaderMap::new();
    companion_headers.insert(header::ORIGIN, origin.parse().unwrap());
    companion_headers.insert(
        header::AUTHORIZATION,
        format!("Bearer {capability}").parse().unwrap(),
    );
    assert_eq!(
        authorized_user(&companion_headers, &verifier)
            .await
            .unwrap()
            .as_str(),
        user_id
    );
    let reply = request(
        app,
        "POST",
        "/api/v1/auth/companion/verify",
        None,
        json!({"access_token":capability}),
    )
    .await;
    assert_eq!(reply.status(), StatusCode::CREATED);
    assert_eq!(json_body(reply).await, json!({"subject":user_id}));

    VerifiedCompanion {
        verifier,
        companion_headers,
        server,
    }
}

async fn check_revoked_sessions(app: &Router, cookie: &str, capability: &str) {
    assert_eq!(
        request(app, "GET", "/api/v1/auth/me", Some(cookie), Value::Null)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(
            app,
            "POST",
            "/api/v1/auth/companion/verify",
            None,
            json!({"access_token":capability})
        )
        .await
        .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(
            app,
            "POST",
            "/api/v1/auth/companion/token",
            None,
            Value::Null
        )
        .await
        .status(),
        StatusCode::UNAUTHORIZED
    );
}
