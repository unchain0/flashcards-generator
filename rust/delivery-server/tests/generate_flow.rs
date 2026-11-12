#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::redundant_closure_for_method_calls
)]
//! End-to-end proof of the server-side generation flow: login, upload the
//! sample PDF, poll the job and download the CSV.

use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode, header},
};
use flashcards_delivery_server::web::{self, WebState};
use flashcards_integrations_server::{auth_crypto::AuthCrypto, web_database::WebDatabase};
use sqlx::postgres::PgPoolOptions;
use std::{
    path::{Path, PathBuf},
    str::FromStr,
    sync::atomic::{AtomicU64, Ordering},
};
use tower::ServiceExt;

const PASSWORD: &str = "fixture-password-0123456789";
static NEXT_TEMP_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_TEMP_ID.fetch_add(1, Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("flashcards-generate-{}-{id}", std::process::id()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn sample_pdf() -> Vec<u8> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("amostra-celula.pdf");
    std::fs::read(path).unwrap()
}

static DATABASE_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

async fn router(_name: &str) -> (Router, TempDir, tokio::sync::MutexGuard<'static, ()>) {
    let guard = DATABASE_LOCK.lock().await;
    let url = std::env::var("FLASHCARDS_TEST_DATABASE_URL")
        .unwrap_or_else(|_| panic!("disposable PostgreSQL URL required"));
    let admin = PgPoolOptions::new()
        .max_connections(1)
        .connect(&url)
        .await
        .unwrap();
    let database_name = "flashcards_generate";
    sqlx::query("DROP DATABASE IF EXISTS flashcards_generate")
        .execute(&admin)
        .await
        .unwrap();
    sqlx::query("CREATE DATABASE flashcards_generate")
        .execute(&admin)
        .await
        .unwrap();
    admin.close().await;
    let options = sqlx::postgres::PgConnectOptions::from_str(&url)
        .unwrap()
        .database(database_name);
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect_with(options)
        .await
        .unwrap();
    let database_url = format!("{}/{}", url.rsplit_once('/').unwrap().0, database_name);
    let crypto = AuthCrypto::new(
        "generate-test-session-secret-0123456789",
        "generate-test-lookup-secret-0123456789",
    )
    .unwrap();
    let database = WebDatabase::connect(&database_url, crypto).await.unwrap();
    database.migrate().await.unwrap();
    database.create_user(PASSWORD).await.unwrap();
    pool.close().await;

    let static_dir = TempDir::new();
    std::fs::write(static_dir.path().join("index.html"), "<!doctype html>").unwrap();
    let app = web::router(
        WebState::new(database, false, 3600),
        static_dir.path(),
        Vec::new(),
    )
    .unwrap();
    (app, static_dir, guard)
}

async fn login(app: &Router) -> String {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/auth/login")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(format!("{{\"password\":\"{PASSWORD}\"}}")))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    response
        .headers()
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned()
}

fn multipart(pdf: &[u8]) -> (String, Vec<u8>) {
    let boundary = "X-BOUNDARY-1234";
    let mut body = Vec::new();
    body.extend_from_slice(
        format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"amostra.pdf\"\r\nContent-Type: application/pdf\r\n\r\n"
        )
        .as_bytes(),
    );
    body.extend_from_slice(pdf);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    (format!("multipart/form-data; boundary={boundary}"), body)
}

async fn json(response: axum::response::Response) -> serde_json::Value {
    let bytes = axum::body::to_bytes(response.into_body(), 65536)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn upload_generates_a_downloadable_cloze_csv() {
    let (app, _static, _guard) = router("main").await;
    let cookie = login(&app).await;

    let (content_type, body) = multipart(&sample_pdf());
    let created = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/generate")
                .header(header::COOKIE, &cookie)
                .header(header::CONTENT_TYPE, content_type)
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(created.status(), StatusCode::ACCEPTED);
    let payload = json(created).await;
    let id = payload["job_id"].as_str().expect("job id").to_owned();

    let status = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/v1/jobs/{id}"))
                .header(header::COOKIE, &cookie)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(status.status(), StatusCode::OK);
    let payload = json(status).await;
    assert_eq!(payload["status"], "done");
    assert!(payload["cards"].as_u64().unwrap() >= 1);

    let csv = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/v1/jobs/{id}/csv"))
                .header(header::COOKIE, &cookie)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(csv.status(), StatusCode::OK);
    assert_eq!(
        csv.headers()[header::CONTENT_TYPE],
        "text/csv; charset=utf-8"
    );
    let bytes = axum::body::to_bytes(csv.into_body(), 65536).await.unwrap();
    let text = String::from_utf8(bytes.to_vec()).unwrap();
    assert!(text.contains("{{c1::"), "{text}");
    assert!(text.contains("\r\n"), "CRLF expected");
}

#[tokio::test]
async fn anonymous_and_unknown_jobs_are_rejected() {
    let (app, _static, _guard) = router("main").await;
    let (content_type, body) = multipart(&sample_pdf());
    let anonymous = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/generate")
                .header(header::CONTENT_TYPE, content_type)
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(anonymous.status(), StatusCode::UNAUTHORIZED);

    let cookie = login(&app).await;
    let missing = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/v1/jobs/unknown")
                .header(header::COOKIE, &cookie)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(missing.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn a_document_without_usable_text_is_rejected() {
    let (app, _static, _guard) = router("main").await;
    let cookie = login(&app).await;
    let (content_type, body) = multipart(b"not a pdf");
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/generate")
                .header(header::COOKIE, &cookie)
                .header(header::CONTENT_TYPE, content_type)
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
}
