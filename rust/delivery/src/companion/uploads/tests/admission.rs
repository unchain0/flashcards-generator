use super::*;
use flashcards_integrations::{
    companion_auth::RemoteTokenVerifier, job_workspace::WorkspaceError,
    notebooklm_browser::NotebookLMBrowserLogin, notebooklm_profiles::LocalNotebookLMProfiles,
};
use std::sync::atomic::{AtomicUsize, Ordering};
use tokio::task::JoinHandle;

#[tokio::test]
async fn authenticates_before_rejecting_overload_without_reading_documents_or_leaking_permits() {
    let (state, server, directory) = fixture().await;
    let app = Router::new()
        .route("/v1/jobs", post(create_job))
        .layer(DefaultBodyLimit::max(MAX_REQUEST_BYTES))
        .with_state(state.clone());
    let reads = Arc::new(AtomicUsize::new(0));
    let held = state.uploads.try_acquire_many(3).unwrap();
    for (token, expected, detail) in [
        (
            "expired-token",
            StatusCode::UNAUTHORIZED,
            "Sessão do aplicativo expirada",
        ),
        (
            "owner-token",
            StatusCode::TOO_MANY_REQUESTS,
            "Aguarde um envio local terminar antes de enviar outro.",
        ),
    ] {
        let response = tokio::time::timeout(
            Duration::from_secs(5),
            app.clone().oneshot(upload_request(&state, token, &reads)),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(response.status(), expected);
        assert_eq!(reads.load(Ordering::SeqCst), 0);
        assert_eq!(state.uploads.available_permits(), 0);
        assert!(!directory.path().join("profiles").exists());
        assert!(state.jobs.start_next().unwrap().is_none());
        let body = to_bytes(response.into_body(), 8192).await.unwrap();
        assert!(!String::from_utf8_lossy(&body).contains("private"));
        assert_eq!(
            serde_json::from_slice::<Value>(&body).unwrap(),
            serde_json::json!({"detail":detail})
        );
    }
    drop(held);
    for _ in 0..4 {
        let response = tokio::time::timeout(
            Duration::from_secs(5),
            app.clone()
                .oneshot(upload_request(&state, "owner-token", &reads)),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(response.status(), StatusCode::CONFLICT);
        assert_eq!(state.uploads.available_permits(), 3);
        assert_eq!(reads.load(Ordering::SeqCst), 0);
        assert!(state.jobs.start_next().unwrap().is_none());
    }
    state.jobs.close().unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn fixture() -> (Arc<CompanionState>, JoinHandle<()>, tempfile::TempDir) {
    let hosted = Router::new().route("/api/v1/auth/companion/verify", post(verify_token));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, hosted).await.unwrap() });
    let directory = tempfile::tempdir().unwrap();
    let state = Arc::new(CompanionState::new(
        RemoteTokenVerifier::new(&origin).unwrap(),
        LocalNotebookLMProfiles::new(directory.path().join("profiles")).unwrap(),
        NotebookLMBrowserLogin::new(None),
        LocalJobStore::new().unwrap(),
    ));
    (state, server, directory)
}

#[tokio::test]
async fn unavailable_profile_checks_preserve_local_data_without_reading_uploads_or_requesting_login()
 {
    let (state, server, _directory) = fixture().await;
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let home = state.profiles.home(&owner).unwrap();
    let saved_session = r#"{"cookies":[{"name":"SID","value":"private-synthetic-cookie","domain":".google.com","path":"/"}],"origins":[]}"#;
    state.profiles.save(&owner, saved_session).unwrap();
    let storage = home.join("profiles/default/storage_state.json");
    let config = home.join("config.json");
    let original = "private-invalid-profile-config";
    std::fs::write(&config, original).unwrap();
    let app = Router::new()
        .route("/v1/jobs", post(create_job))
        .with_state(state.clone());
    let reads = Arc::new(AtomicUsize::new(0));
    for _ in 0..4 {
        let response = tokio::time::timeout(
            Duration::from_secs(5),
            app.clone()
                .oneshot(upload_request(&state, "owner-token", &reads)),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        let body = to_bytes(response.into_body(), 8192).await.unwrap();
        assert_eq!(
            serde_json::from_slice::<Value>(&body).unwrap(),
            serde_json::json!({"detail":"Não foi possível verificar sua sessão do NotebookLM. Tente novamente."})
        );
        assert!(!String::from_utf8_lossy(&body).contains("private"));
        assert_eq!(reads.load(Ordering::SeqCst), 0);
        assert_eq!(state.uploads.available_permits(), 3);
        assert!(state.jobs.start_next().unwrap().is_none());
        assert_eq!(std::fs::read_to_string(&config).unwrap(), original);
        assert_eq!(std::fs::read_to_string(&storage).unwrap(), saved_session);
    }
    state.jobs.close().unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn verify_token(Json(body): Json<Value>) -> (StatusCode, Json<Value>) {
    if body["access_token"] == "owner-token" {
        (
            StatusCode::CREATED,
            Json(serde_json::json!({"subject":"a".repeat(32)})),
        )
    } else {
        (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"detail":"private-auth-failure"})),
        )
    }
}

fn upload_request(
    state: &CompanionState,
    token: &str,
    reads: &Arc<AtomicUsize>,
) -> HttpRequest<Body> {
    let reads = reads.clone();
    let body = Body::from_stream(futures_util::stream::once(async move {
        reads.fetch_add(1, Ordering::SeqCst);
        Ok::<_, std::io::Error>(b"private-source-document".as_slice())
    }));
    HttpRequest::post("/v1/jobs")
        .header("origin", state.verifier.origin())
        .header("authorization", format!("Bearer {token}"))
        .header(
            "content-type",
            "multipart/form-data; boundary=private-source",
        )
        .body(body)
        .unwrap()
}

#[tokio::test]
async fn unexpected_upload_failures_hide_internal_causes_in_http_responses() {
    use axum::response::IntoResponse;

    for error in [
        UploadError::Io(std::io::Error::other("private-local-path")),
        UploadError::Store(StoreError::Workspace(WorkspaceError::Io(
            std::io::Error::other("private-workspace-cause"),
        ))),
        UploadError::Store(StoreError::Unavailable),
        UploadError::Store(StoreError::Registry(RegistryError::InvalidState)),
    ] {
        let response = ApiError::Upload(error).into_response();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        let body = to_bytes(response.into_body(), 8192).await.unwrap();
        assert_eq!(
            serde_json::from_slice::<Value>(&body).unwrap(),
            serde_json::json!({"detail":"Falha local ao receber arquivos."})
        );
        assert!(!String::from_utf8_lossy(&body).contains("private"));
    }
}

#[tokio::test]
async fn oversized_files_report_the_upload_limit_as_a_client_error() {
    use axum::response::IntoResponse;

    let response = ApiError::Upload(UploadError::TooLarge).into_response();
    assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);
    let body = to_bytes(response.into_body(), 8192).await.unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap(),
        serde_json::json!({"detail":"Os arquivos excedem o limite de tamanho permitido."})
    );
}
