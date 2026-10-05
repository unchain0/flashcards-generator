use super::*;
use flashcards_integrations::{
    companion_auth::RemoteTokenVerifier, notebooklm_browser::NotebookLMBrowserLogin,
    notebooklm_profiles::LocalNotebookLMProfiles,
};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

#[derive(Default)]
struct Verification {
    calls: AtomicUsize,
    outcome: AtomicUsize,
}

async fn verify(
    State(state): State<Arc<Verification>>,
    Json(body): Json<Value>,
) -> (StatusCode, Json<Value>) {
    assert_eq!(body, serde_json::json!({"access_token":"owner-token"}));
    let previous = state.calls.fetch_add(1, Ordering::SeqCst);
    let outcome = if previous == 0 {
        0
    } else {
        state.outcome.load(Ordering::SeqCst)
    };
    match outcome {
        0 => (
            StatusCode::CREATED,
            Json(serde_json::json!({"subject":"a".repeat(32)})),
        ),
        1 => (
            StatusCode::CREATED,
            Json(serde_json::json!({"subject":"b".repeat(32)})),
        ),
        2 => (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"detail":"private-revoked-session"})),
        ),
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"detail":"private-server-failure"})),
        ),
    }
}

async fn post_admission(
    State(state): State<Arc<CompanionState>>,
    request: Request,
) -> Result<(StatusCode, Json<JobRead>), ApiError> {
    let owner = authorized_user(request.headers(), &state.verifier)
        .await
        .map_err(ApiError::Authorization)?;
    let snapshot = receive_owned_upload(&state, owner, request).await?;
    Ok((StatusCode::ACCEPTED, Json(snapshot.into())))
}

async fn fixture() -> (
    Router,
    Arc<CompanionState>,
    Arc<Verification>,
    tokio::task::JoinHandle<()>,
    tempfile::TempDir,
) {
    let verification = Arc::new(Verification::default());
    let hosted = Router::new()
        .route("/api/v1/auth/companion/verify", post(verify))
        .with_state(verification.clone());
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
    let app = Router::new()
        .route("/upload", post(post_admission))
        .with_state(state.clone());
    (app, state, verification, server, directory)
}

fn authorized(mut request: HttpRequest<Body>, state: &CompanionState) -> HttpRequest<Body> {
    request
        .headers_mut()
        .insert("origin", state.verifier.origin().parse().unwrap());
    request
        .headers_mut()
        .insert("authorization", "Bearer owner-token".parse().unwrap());
    request
}

#[tokio::test]
async fn rechecks_session_identity_and_availability_after_receiving_files_before_queuing() {
    let (app, state, verification, server, directory) = fixture().await;
    for (outcome, expected) in [
        (0, StatusCode::ACCEPTED),
        (1, StatusCode::UNAUTHORIZED),
        (2, StatusCode::UNAUTHORIZED),
        (3, StatusCode::SERVICE_UNAVAILABLE),
    ] {
        verification.calls.store(0, Ordering::SeqCst);
        verification.outcome.store(outcome, Ordering::SeqCst);
        let upload = authorized(
            request(&[("files", Some("study.pdf"), b"private-source")]),
            &state,
        );
        let response = app.clone().oneshot(upload).await.unwrap();
        assert_eq!(response.status(), expected);
        assert_eq!(verification.calls.load(Ordering::SeqCst), 2);
        let body = to_bytes(response.into_body(), 8192).await.unwrap();
        assert!(!String::from_utf8_lossy(&body).contains("private"));
        check_queued_source(outcome, &state.jobs);
        assert!(!directory.path().join("profiles").exists());
    }
    state.jobs.close().unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

struct StreamDropped(Arc<AtomicBool>);

fn check_queued_source(outcome: usize, jobs: &LocalJobStore) {
    if outcome == 0 {
        let execution = jobs.start_next().unwrap().unwrap();
        let path = execution
            .workspace()
            .unwrap()
            .input_dir()
            .join("01-study.pdf");
        assert_eq!(std::fs::read(&path).unwrap(), b"private-source");
        assert_eq!(execution.owner().as_str(), "a".repeat(32));
        drop(execution);
        assert!(!path.exists());
    }
    assert!(jobs.start_next().unwrap().is_none());
}

#[tokio::test]
async fn interrupted_file_bodies_are_discarded_before_final_authorization_or_queuing() {
    use futures_util::StreamExt;
    use std::io;

    let (app, state, verification, server, _directory) = fixture().await;
    let waiting = Arc::new(tokio::sync::Notify::new());
    let signal = waiting.clone();
    let (release, resume) = tokio::sync::oneshot::channel();
    let prefix = b"--test-boundary\r\nContent-Disposition: form-data; name=\"files\"; filename=\"study.pdf\"\r\n\r\nprivate-source";
    let stream = futures_util::stream::once(async { Ok::<_, io::Error>(prefix.as_slice()) }).chain(
        futures_util::stream::once(async move {
            signal.notify_one();
            resume.await.unwrap();
            Err(io::Error::new(
                io::ErrorKind::ConnectionReset,
                "private-stream-failure",
            ))
        }),
    );
    let upload = HttpRequest::post("/upload")
        .header(
            "content-type",
            "multipart/form-data; boundary=test-boundary",
        )
        .body(Body::from_stream(stream))
        .unwrap();
    let task = tokio::spawn(app.oneshot(authorized(upload, &state)));
    tokio::time::timeout(Duration::from_secs(5), waiting.notified())
        .await
        .unwrap();
    release.send(()).unwrap();
    let response = task.await.unwrap().unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), 8192).await.unwrap();
    assert!(!String::from_utf8_lossy(&body).contains("private"));
    assert_eq!(verification.calls.load(Ordering::SeqCst), 1);
    assert!(state.jobs.start_next().unwrap().is_none());
    state.jobs.close().unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

impl Drop for StreamDropped {
    fn drop(&mut self) {
        self.0.store(true, Ordering::SeqCst);
    }
}

#[tokio::test]
async fn upload_deadline_drops_pending_input_without_queueing_or_rechecking_credentials() {
    use futures_util::StreamExt;
    use std::{io, task::Poll};

    let (app, state, verification, server, _directory) = fixture().await;
    let waiting = Arc::new(tokio::sync::Notify::new());
    let signal = waiting.clone();
    let dropped = Arc::new(AtomicBool::new(false));
    let guard = StreamDropped(dropped.clone());
    let prefix = b"--test-boundary\r\nContent-Disposition: form-data; name=\"files\"; filename=\"study.pdf\"\r\n\r\nprivate-source";
    let stream = futures_util::stream::once(async { Ok::<_, io::Error>(prefix.as_slice()) }).chain(
        futures_util::stream::poll_fn(move |_| {
            let _guard = &guard;
            signal.notify_one();
            Poll::Pending::<Option<Result<&[u8], io::Error>>>
        }),
    );
    let upload = HttpRequest::post("/upload")
        .header(
            "content-type",
            "multipart/form-data; boundary=test-boundary",
        )
        .body(Body::from_stream(stream))
        .unwrap();
    let task = tokio::spawn(app.oneshot(authorized(upload, &state)));
    tokio::time::timeout(Duration::from_secs(5), waiting.notified())
        .await
        .unwrap();
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(601)).await;
    let response = task.await.unwrap().unwrap();
    tokio::time::resume();
    assert_eq!(response.status(), StatusCode::REQUEST_TIMEOUT);
    let body = to_bytes(response.into_body(), 8192).await.unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap(),
        serde_json::json!({"detail":"O envio dos arquivos demorou demais. Tente novamente."})
    );
    assert!(dropped.load(Ordering::SeqCst));
    assert_eq!(verification.calls.load(Ordering::SeqCst), 1);
    assert!(state.jobs.start_next().unwrap().is_none());
    state.jobs.close().unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}
