use super::*;
use axum::{
    Form, Router,
    body::Bytes,
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use flashcards_services::notebooklm::NotebookLMGateway;
use std::{
    collections::BTreeMap,
    error::Error,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};
use tokio::sync::Notify;

#[derive(Clone)]
struct Fixture {
    origin: String,
    sources: Arc<Mutex<BTreeMap<String, String>>>,
    sequence: Arc<AtomicUsize>,
    canceled: Arc<AtomicUsize>,
    uploaded: Arc<Mutex<Vec<Vec<u8>>>>,
    finalizing: Arc<Notify>,
}

#[test]
fn reports_upload_cleanup_failure_when_cancellation_occurs_outside_a_runtime() {
    let tasks: CleanupTasks = Arc::default();
    let http = reqwest::Client::new();
    let cleanup = UploadCleanup {
        remove: Some(http.post("http://127.0.0.1:9/synthetic-delete")),
        cancel: Some(http.post("http://127.0.0.1:9/synthetic-cancel")),
        tasks: Some(tasks.clone()),
    };
    drop(cleanup);
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    assert!(!runtime.block_on(super::super::drain_cleanup(&tasks)));
    assert!(runtime.block_on(super::super::drain_cleanup(&tasks)));
}

#[cfg(unix)]
#[test]
fn rejects_uploads_replaced_after_their_initial_metadata_check() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("source.pdf");
    let preserved = directory.path().join("preserved.pdf");
    std::fs::write(&source, b"synthetic original PDF").unwrap();
    let result = super::super::tests::filesystem_races::between_metadata_and_open(
        open_upload(&source),
        || {
            std::fs::rename(&source, &preserved).unwrap();
            std::fs::write(&source, b"synthetic replacement PDF").unwrap();
        },
    );
    assert!(matches!(result, Err(NotebookLMError::InvalidInput)));
    assert_eq!(
        std::fs::read(&preserved).unwrap(),
        b"synthetic original PDF"
    );
    assert_eq!(
        std::fs::read(&source).unwrap(),
        b"synthetic replacement PDF"
    );
}

#[cfg(target_os = "linux")]
#[test]
fn rejects_credentials_and_uploads_redirected_to_a_different_device() {
    use std::os::unix::fs::{MetadataExt, symlink};
    let original = tempfile::tempdir().unwrap();
    let replacement = tempfile::tempdir_in("/dev/shm").unwrap();
    assert_ne!(
        std::fs::metadata(original.path()).unwrap().dev(),
        std::fs::metadata(replacement.path()).unwrap().dev()
    );
    for directory in [original.path(), replacement.path()] {
        std::fs::write(directory.join("source.pdf"), b"synthetic private file").unwrap();
    }
    let alias = original.path().join("current");
    let source = alias.join("source.pdf");
    let redirect = || {
        std::fs::remove_file(&alias).unwrap();
        symlink(replacement.path(), &alias).unwrap();
    };
    symlink(original.path(), &alias).unwrap();
    let credentials = super::super::tests::filesystem_races::between_metadata_and_open(
        super::super::read_storage_file(&source),
        redirect,
    );
    assert!(matches!(credentials, Err(NotebookLMError::Authentication)));
    std::fs::remove_file(&alias).unwrap();
    symlink(original.path(), &alias).unwrap();
    let upload = super::super::tests::filesystem_races::between_metadata_and_open(
        open_upload(&source),
        redirect,
    );
    assert!(matches!(upload, Err(NotebookLMError::InvalidInput)));
    std::fs::remove_file(&alias).unwrap();
    for directory in [original.path(), replacement.path()] {
        assert_eq!(
            std::fs::read(directory.join("source.pdf")).unwrap(),
            b"synthetic private file"
        );
    }
}

fn framed(method: &str, value: &Value) -> String {
    let frame = json!([["wrb.fr", method, value.to_string()]]).to_string();
    format!(")]}}'\n{}\n{frame}\n", frame.len())
}

async fn rpc(
    State(state): State<Fixture>,
    headers: HeaderMap,
    Query(query): Query<BTreeMap<String, String>>,
    Form(form): Form<BTreeMap<String, String>>,
) -> Response {
    assert_eq!(headers["x-goog-authuser"], "learner+study@example.test");
    assert_eq!(query["authuser"], "learner+study@example.test");
    let request: Value = serde_json::from_str(&form["f.req"]).unwrap();
    let method = request[0][0][0].as_str().unwrap();
    let params: Value = serde_json::from_str(request[0][0][1].as_str().unwrap()).unwrap();
    let value = match method {
        "rLM1Ne" => {
            let mut rows = vec![json!([
                ["existing-source"],
                "Existing.pdf",
                null,
                [null, 2]
            ])];
            rows.extend(
                state
                    .sources
                    .lock()
                    .unwrap()
                    .iter()
                    .map(|(id, name)| json!([[id], name, null, [null, 2]])),
            );
            json!([["Notebook", rows, "notebook-1"]])
        }
        "o4cbdc" => {
            let name = params[0][0][0].as_str().unwrap();
            assert_eq!(params[1], "notebook-1");
            assert_eq!(
                params[2],
                json!([
                    2,
                    null,
                    null,
                    [1, null, null, null, null, null, null, null, null, null, [1]]
                ])
            );
            let id = if name == "existing.pdf" {
                "existing-source".to_owned()
            } else {
                format!("source-{}", state.sequence.fetch_add(1, Ordering::SeqCst))
            };
            if id != "existing-source" {
                state
                    .sources
                    .lock()
                    .unwrap()
                    .insert(id.clone(), name.to_owned());
            }
            let row = json!([[id], name, [null, null, null, null, 0]]);
            json!([[row], null, [[[row]]]])
        }
        "tGMBJ" => {
            let id = params[0][0][0].as_str().unwrap();
            assert_ne!(id, "existing-source");
            let pending = state
                .sources
                .lock()
                .unwrap()
                .get(id)
                .is_some_and(|name| name == "cancel-abort-removal.pdf");
            if pending {
                state.finalizing.notify_one();
                std::future::pending::<()>().await;
            }
            let mut sources = state.sources.lock().unwrap();
            if sources.get(id).is_some_and(|name| {
                matches!(
                    name.as_str(),
                    "cleanup-fail.pdf" | "cancel-cleanup-fail.pdf"
                )
            }) {
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
            sources.remove(id);
            Value::Null
        }
        _ => panic!("unexpected upload RPC"),
    };
    (StatusCode::OK, framed(method, &value)).into_response()
}

async fn upload(
    State(state): State<Fixture>,
    headers: HeaderMap,
    Query(query): Query<BTreeMap<String, String>>,
    body: Bytes,
) -> Response {
    assert_eq!(headers[header::COOKIE], "SID=synthetic-session");
    assert_eq!(headers[header::ORIGIN], state.origin);
    assert_eq!(headers["x-goog-authuser"], "learner+study@example.test");
    match headers["x-goog-upload-command"].to_str().unwrap() {
        "start" => {
            assert_eq!(query["authuser"], "learner+study@example.test");
            assert_eq!(headers["x-goog-upload-protocol"], "resumable");
            let data: Value = serde_json::from_slice(&body).unwrap();
            assert_eq!(data["PROJECT_ID"], "notebook-1");
            let name = data["SOURCE_NAME"].as_str().unwrap();
            assert_eq!(headers["x-goog-upload-header-content-length"], "131073");
            let expected = if Path::new(name)
                .extension()
                .and_then(std::ffi::OsStr::to_str)
                .is_some_and(|extension| extension.eq_ignore_ascii_case("pptx"))
            {
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            } else {
                "application/pdf"
            };
            assert_eq!(headers["x-goog-upload-header-content-type"], expected);
            if name == "start-fail.pdf" {
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
            let target = match name {
                "bad-url.pdf" => "https://attacker.example/upload/_/?upload_id=private".to_owned(),
                "malformed-url.pdf" => {
                    "https://[invalid/upload/_/?upload_id=synthetic-private-session".to_owned()
                }
                _ => format!(
                    "{}/upload/_/?upload_id={}",
                    state.origin,
                    data["SOURCE_ID"].as_str().unwrap()
                ),
            };
            (StatusCode::OK, [("x-goog-upload-url", target)]).into_response()
        }
        "upload, finalize" => {
            assert_eq!(headers["x-goog-upload-offset"], "0");
            assert_eq!(headers[header::CONTENT_LENGTH], "131073");
            assert_eq!(body.len(), 131_073);
            assert!(body.iter().all(|byte| *byte == 0x5a));
            let name = state.sources.lock().unwrap()[&query["upload_id"]].clone();
            if matches!(
                name.as_str(),
                "cancel.pdf"
                    | "cancel-cleanup-fail.pdf"
                    | "cancel-abort-cleanup.pdf"
                    | "cancel-abort-removal.pdf"
            ) {
                state.finalizing.notify_one();
                tokio::time::sleep(Duration::from_secs(30)).await;
            }
            if name == "finalize-fail.pdf" || name == "cleanup-fail.pdf" {
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
            state.uploaded.lock().unwrap().push(body.to_vec());
            StatusCode::OK.into_response()
        }
        "cancel" => {
            assert!(body.is_empty());
            state.canceled.fetch_add(1, Ordering::SeqCst);
            let pending =
                state.sources.lock().unwrap()[&query["upload_id"]] == "cancel-abort-cleanup.pdf";
            if pending {
                state.finalizing.notify_one();
                std::future::pending::<()>().await;
            }
            StatusCode::OK.into_response()
        }
        _ => panic!("unexpected upload command"),
    }
}

#[tokio::test]
async fn streams_documents_and_cleans_failed_or_canceled_uploads_without_touching_existing_sources()
{
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let state = Fixture {
        origin: format!("http://{}", listener.local_addr().unwrap()),
        sources: Arc::default(),
        sequence: Arc::new(AtomicUsize::new(1)),
        canceled: Arc::default(),
        uploaded: Arc::default(),
        finalizing: Arc::new(Notify::new()),
    };
    let app = Router::new()
        .route(
            "/",
            get(|Query(query): Query<BTreeMap<String, String>>| async move {
                assert_eq!(query["authuser"], "learner+study@example.test");
                r#"{"SNlM0e":"csrf","FdrFJe":"sid","cfb2h":"build"}"#
            }),
        )
        .route(super::super::RPC_PATH, post(rpc))
        .route(UPLOAD_PATH, post(upload))
        .with_state(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let raw = r#"{"authuser":0,"notebooklm":{"version":1,"account":{"authuser":3,"email":"learner+study@example.test"}},"cookies":[{"name":"SID","value":"synthetic-session","domain":".google.com","path":"/"}]}"#;
    let client = Arc::new(NotebookLMClient::connect(raw, &state.origin).await.unwrap());
    let files = tempfile::tempdir().unwrap();
    for name in [
        "document.pdf",
        "slides.pptx",
        "existing.pdf",
        "bad-url.pdf",
        "malformed-url.pdf",
        "start-fail.pdf",
        "finalize-fail.pdf",
        "cleanup-fail.pdf",
        "cancel.pdf",
        "cancel-cleanup-fail.pdf",
        "cancel-abort-cleanup.pdf",
        "cancel-abort-removal.pdf",
    ] {
        std::fs::write(files.path().join(name), vec![0x5a; 131_073]).unwrap();
    }
    check_upload_success_and_failures(&client, &state, &files).await;
    check_upload_cancellation(&client, &state, &files).await;
    check_aborted_upload_cleanup(&client, &state, &files).await;
    check_invalid_uploads(&client, &state, &files).await;
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

#[test]
fn validates_upload_destinations_and_registration_envelopes() {
    for host in ["notebooklm.google.com", "notebook.google.com"] {
        assert!(
            validated_upload_url(
                &format!("https://{host}/upload/_/?upload_id=synthetic"),
                ORIGIN
            )
            .is_ok()
        );
    }
    for url in [
        "http://notebooklm.google.com/upload/_/?upload_id=x",
        "https://notebooklm.google.com.evil/upload/_/?upload_id=x",
        "https://notebooklm.google.com:444/upload/_/?upload_id=x",
        "https://notebooklm.google.com/upload/_/?upload_id=",
        "https://notebooklm.google.com/upload/_/?upload_id=x&upload_id=y",
        "https://user@notebooklm.google.com/upload/_/?upload_id=x",
        "https://@notebooklm.google.com/upload/_/?upload_id=x",
        "https://notebooklm.google.com/other?upload_id=x",
        "https://notebooklm.google.com/upload/_/?upload_id=x#fragment",
    ] {
        assert!(validated_upload_url(url, ORIGIN).is_err(), "{url}");
    }
    for result in [
        json!({"SOURCE_ID":"source-1","SOURCE_NAME":"file.pdf"}),
        json!([["source-1"]]),
        json!([null, [["source-1"]]]),
        json!([
            [[["source-1"], "file.pdf", [null, null, null, null, 0]]],
            null,
            [[[["source-1"], "file.pdf", [null, null, null, null, 0]]]]
        ]),
        json!([[["unrelated-id"], "other.pdf"], [["source-1"], "file.pdf"]]),
    ] {
        assert_eq!(registered_id(&result, "file.pdf").unwrap(), "source-1");
    }
    for result in [
        json!({"SOURCE_ID":"source-1","SOURCE_NAME":"other.pdf"}),
        json!({"SOURCE_ID":"source-1","sourceId":"source-2"}),
        json!([["source-1", "source-2"]]),
        json!(null),
        json!([["file.pdf"]]),
        json!([[["source-1"], "file.pdf"], [["source-2"], "file.pdf"]]),
        json!([[["source-1"], "other.pdf"]]),
    ] {
        assert!(registered_id(&result, "file.pdf").is_err());
    }
    let mut nested = json!([["source-1"], "file.pdf"]);
    for _ in 0..9 {
        nested = json!([nested, null]);
    }
    for result in [nested, json!(vec![Value::Null; 257])] {
        assert!(matches!(
            registered_id(&result, "file.pdf"),
            Err(NotebookLMError::Schema("upload source registration"))
        ));
    }
}

#[tokio::test]
async fn registers_empty_notebooks_and_rejects_invalid_baselines_before_creating_sources() {
    let mode = Arc::new(AtomicUsize::new(0));
    let registrations = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route(
            "/",
            get(|| async { r#"{"SNlM0e":"csrf","FdrFJe":"sid","cfb2h":"build"}"# }),
        )
        .route(super::super::RPC_PATH, post(registration_baseline_response))
        .with_state((mode.clone(), registrations.clone()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let raw = r#"{"cookies":[{"name":"SID","value":"synthetic-session","domain":".google.com"}]}"#;
    let client = NotebookLMClient::connect(raw, &origin).await.unwrap();
    for selected in 0..=4 {
        mode.store(selected, Ordering::SeqCst);
        let result = client.register_upload("notebook-1", "file.pdf").await;
        match selected {
            0..=1 => assert_eq!(result.unwrap(), "new-source"),
            _ => assert!(result.is_err()),
        }
        assert_eq!(registrations.load(Ordering::SeqCst), (selected + 1).min(2));
    }
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn registration_baseline_response(
    State((mode, registrations)): State<(Arc<AtomicUsize>, Arc<AtomicUsize>)>,
    Form(form): Form<BTreeMap<String, String>>,
) -> String {
    let request: Value = serde_json::from_str(&form["f.req"]).unwrap();
    let method = request[0][0][0].as_str().unwrap();
    let value = match method {
        "rLM1Ne" => match mode.load(Ordering::SeqCst) {
            0 => json!([["Notebook", null, "notebook-1"]]),
            1 => json!([["Notebook", [], "notebook-1"]]),
            2 => json!([["Notebook", "invalid", "notebook-1"]]),
            3 => json!([["Notebook", [[["../invalid"]]], "notebook-1"]]),
            _ => json!({}),
        },
        "o4cbdc" => {
            registrations.fetch_add(1, Ordering::SeqCst);
            json!([[["new-source"], "file.pdf"]])
        }
        _ => panic!("unexpected registration RPC"),
    };
    framed(method, &value)
}

#[test]
fn upload_url_limits_preserve_opaque_session_ids_and_reject_ambiguous_or_malformed_targets() {
    let prefix = format!("{ORIGIN}{UPLOAD_PATH}?upload_id=");
    let exact = format!("{prefix}{}", "x".repeat(16_384 - prefix.len()));
    assert_eq!(
        validated_upload_url(&exact, ORIGIN).unwrap().as_str(),
        exact
    );
    let excessive = format!("{exact}x");
    for raw in [
        excessive.as_str(),
        "",
        "/upload/_/?upload_id=synthetic-private-session",
        "https://[invalid/upload/_/?upload_id=synthetic-private-session",
        "https://notebook.google.com/upload/_/?authuser=2",
        "https://notebook.google.com/upload/_/?upload_id=x&UPLOAD_ID=synthetic-private-session",
        "https://notebook.google.com/upload/_/?upload_id=x&%75pload_id=synthetic-private-session",
    ] {
        let error = validated_upload_url(raw, ORIGIN).unwrap_err();
        assert!(matches!(
            error,
            NotebookLMError::Schema("untrusted upload URL")
        ));
        assert!(!format!("{error:?}").contains("synthetic-private-session"));
        assert!(error.source().is_none());
    }
    let raw = format!("{prefix}synthetic%2Fsession%2Btoken%3D&authuser=2");
    let target = validated_upload_url(&raw, ORIGIN).unwrap();
    assert_eq!(target.as_str(), raw);
    assert_eq!(
        target.query_pairs().next().unwrap().1,
        "synthetic/session+token="
    );
    assert!(
        validated_upload_url(
            "http://127.0.0.1:8766/upload/_/?upload_id=synthetic",
            "http://127.0.0.1:8766"
        )
        .is_ok()
    );
    assert!(
        validated_upload_url(
            "http://127.0.0.1:8767/upload/_/?upload_id=synthetic",
            "http://127.0.0.1:8766"
        )
        .is_err()
    );
}

async fn check_upload_success_and_failures(
    client: &NotebookLMClient,
    state: &Fixture,
    files: &tempfile::TempDir,
) {
    for name in ["document.pdf", "slides.pptx"] {
        let id = client
            .add_file_source("notebook-1", &files.path().join(name))
            .await
            .unwrap();
        assert_eq!(state.sources.lock().unwrap()[&id], name);
    }
    assert_eq!(state.uploaded.lock().unwrap().len(), 2);
    assert!(matches!(
        client
            .add_file_source("notebook-1", &files.path().join("existing.pdf"))
            .await,
        Err(NotebookLMError::Schema("ambiguous upload registration"))
    ));
    for name in [
        "bad-url.pdf",
        "malformed-url.pdf",
        "start-fail.pdf",
        "finalize-fail.pdf",
    ] {
        let error = client
            .add_file_source("notebook-1", &files.path().join(name))
            .await
            .unwrap_err();
        assert!(
            matches!(
                error,
                NotebookLMError::Upload {
                    cleanup_failed: false,
                    ..
                }
            ),
            "{error:?}"
        );
        assert!(
            !state
                .sources
                .lock()
                .unwrap()
                .values()
                .any(|value| value == name)
        );
        assert!(!format!("{error:?}").contains("upload_id"));
        assert!(!format!("{error:?}").contains("synthetic-private-session"));
        assert!(matches!(
            &error,
            NotebookLMError::Upload { cause, .. }
                if !name.ends_with("-url.pdf")
                    || matches!(cause.as_ref(), NotebookLMError::Schema("untrusted upload URL"))
        ));
    }
    assert_eq!(state.uploaded.lock().unwrap().len(), 2);
    assert_eq!(state.canceled.load(Ordering::SeqCst), 1);
    assert!(matches!(
        client
            .add_file_source("notebook-1", &files.path().join("cleanup-fail.pdf"))
            .await,
        Err(NotebookLMError::Upload {
            cleanup_failed: true,
            ..
        })
    ));
}

async fn check_upload_cancellation(
    client: &Arc<NotebookLMClient>,
    state: &Fixture,
    files: &tempfile::TempDir,
) {
    let worker = Arc::clone(client);
    let path = files.path().join("cancel.pdf");
    let job = tokio::spawn(async move { worker.add_file_source("notebook-1", &path).await });
    tokio::time::timeout(Duration::from_secs(2), state.finalizing.notified())
        .await
        .unwrap();
    job.abort();
    assert!(job.await.unwrap_err().is_cancelled());
    assert!(client.finish_cleanup().await);
    tokio::time::timeout(Duration::from_secs(2), wait_for_canceled_source(state))
        .await
        .unwrap();
    assert_eq!(state.canceled.load(Ordering::SeqCst), 3);
    assert_eq!(state.uploaded.lock().unwrap().len(), 2);
    let worker = Arc::clone(client);
    let path = files.path().join("cancel-cleanup-fail.pdf");
    let job = tokio::spawn(async move { worker.add_file_source("notebook-1", &path).await });
    tokio::time::timeout(Duration::from_secs(2), state.finalizing.notified())
        .await
        .unwrap();
    job.abort();
    assert!(job.await.unwrap_err().is_cancelled());
    assert!(!client.finish_cleanup().await);
    assert!(
        state
            .sources
            .lock()
            .unwrap()
            .values()
            .any(|name| name == "cancel-cleanup-fail.pdf")
    );
    assert_eq!(state.canceled.load(Ordering::SeqCst), 4);
    assert_eq!(state.uploaded.lock().unwrap().len(), 2);
}

async fn check_invalid_uploads(
    client: &NotebookLMClient,
    state: &Fixture,
    files: &tempfile::TempDir,
) {
    let registrations = state.sequence.load(Ordering::SeqCst);
    let excessive_name = files.path().join(format!("{}.pdf", "x".repeat(252)));
    assert!(matches!(
        client.add_file_source("notebook-1", &excessive_name).await,
        Err(NotebookLMError::InvalidInput)
    ));
    let control_name = files.path().join("private\nname.pdf");
    std::fs::write(&control_name, b"synthetic document").unwrap();
    assert!(matches!(
        client.add_file_source("notebook-1", &control_name).await,
        Err(NotebookLMError::InvalidInput)
    ));
    let empty = files.path().join("empty.pdf");
    std::fs::write(&empty, []).unwrap();
    assert!(client.add_file_source("notebook-1", &empty).await.is_err());
    let huge = files.path().join("huge.pdf");
    std::fs::File::create(&huge)
        .unwrap()
        .set_len(MAX_FILE_BYTES + 1)
        .unwrap();
    assert!(matches!(
        client.add_file_source("notebook-1", &huge).await,
        Err(NotebookLMError::ResponseTooLarge)
    ));
    assert!(
        client
            .add_file_source("../bad", &files.path().join("document.pdf"))
            .await
            .is_err()
    );
    assert!(
        client
            .add_file_source("notebook-1", &files.path().join("missing.pdf"))
            .await
            .is_err()
    );
    assert!(
        client
            .add_file_source("notebook-1", &files.path().join("unsupported.txt"))
            .await
            .is_err()
    );
    #[cfg(unix)]
    {
        let link = files.path().join("link.pdf");
        std::os::unix::fs::symlink(files.path().join("document.pdf"), &link).unwrap();
        assert!(client.add_file_source("notebook-1", &link).await.is_err());
    }
    assert_eq!(state.sequence.load(Ordering::SeqCst), registrations);
    assert_eq!(
        std::fs::read(files.path().join("document.pdf")).unwrap(),
        vec![0x5a; 131_073]
    );
}

async fn check_aborted_upload_cleanup(
    client: &Arc<NotebookLMClient>,
    state: &Fixture,
    files: &tempfile::TempDir,
) {
    for (name, canceled) in [
        ("cancel-abort-cleanup.pdf", 5),
        ("cancel-abort-removal.pdf", 6),
    ] {
        let worker = Arc::clone(client);
        let path = files.path().join(name);
        let job = tokio::spawn(async move { worker.add_file_source("notebook-1", &path).await });
        tokio::time::timeout(Duration::from_secs(2), state.finalizing.notified())
            .await
            .unwrap();
        job.abort();
        assert!(job.await.unwrap_err().is_cancelled());
        tokio::time::timeout(Duration::from_secs(2), state.finalizing.notified())
            .await
            .unwrap();
        client
            .cleanup_tasks
            .lock()
            .unwrap()
            .last()
            .unwrap()
            .as_ref()
            .unwrap()
            .abort();
        assert!(!client.finish_cleanup().await);
        assert_eq!(client.cleanup_tasks.lock().unwrap().len(), 0);
        assert!(
            state
                .sources
                .lock()
                .unwrap()
                .values()
                .any(|source| source == name)
        );
        assert_eq!(state.canceled.load(Ordering::SeqCst), canceled);
        assert_eq!(state.uploaded.lock().unwrap().len(), 2);
        assert_eq!(
            std::fs::read(files.path().join(name)).unwrap(),
            vec![0x5a; 131_073]
        );
    }
}

async fn wait_for_canceled_source(state: &Fixture) {
    while state
        .sources
        .lock()
        .unwrap()
        .values()
        .any(|name| name == "cancel.pdf")
    {
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}
