use super::*;
use axum::{
    Router,
    body::{Body, to_bytes},
    extract::DefaultBodyLimit,
    http::Request as HttpRequest,
    routing::post,
};
use flashcards_domain::identity::UserId;
use flashcards_integrations::local_job_store::LocalJobStore;
use serde_json::Value;
use tower::ServiceExt;

mod admission;
mod authorization;

async fn submit(
    State(jobs): State<LocalJobStore>,
    request: Request,
) -> Result<(StatusCode, Json<JobRead>), ApiError> {
    let (uploads, options) = receive_upload(request)
        .await
        .map_err(fixture_receive_error)?;
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let snapshot = uploads
        .submit(&jobs, owner, options)
        .await
        .map_err(fixture_submit_error)?;
    Ok((StatusCode::ACCEPTED, Json(snapshot.into())))
}

fn fixture_receive_error(error: ApiError) -> ApiError {
    if let ApiError::Upload(UploadError::Io(cause)) = &error {
        panic!("Unexpected fixture receive failure: {cause:?}");
    }
    error
}

fn fixture_submit_error(error: UploadError) -> ApiError {
    match &error {
        UploadError::Store(
            StoreError::Unavailable
            | StoreError::Workspace(_)
            | StoreError::Registry(RegistryError::Missing | RegistryError::InvalidState),
        )
        | UploadError::Io(_) => panic!("Unexpected fixture submission failure: {error:?}"),
        _ => ApiError::Upload(error),
    }
}

fn app(jobs: LocalJobStore, limit: usize) -> Router {
    Router::new()
        .route("/upload", post(submit))
        .layer(DefaultBodyLimit::max(limit))
        .with_state(jobs)
}
fn request(parts: &[(&str, Option<&str>, &[u8])]) -> HttpRequest<Body> {
    let mut body = Vec::new();
    for (name, filename, value) in parts {
        body.extend_from_slice(
            format!("--test-boundary\r\nContent-Disposition: form-data; name=\"{name}\"")
                .as_bytes(),
        );
        if let Some(filename) = filename {
            body.extend_from_slice(format!("; filename=\"{filename}\"").as_bytes());
        }
        body.extend_from_slice(b"\r\n\r\n");
        body.extend_from_slice(value);
        body.extend_from_slice(b"\r\n");
    }
    body.extend_from_slice(b"--test-boundary--\r\n");
    HttpRequest::post("/upload")
        .header(
            "content-type",
            "multipart/form-data; boundary=test-boundary",
        )
        .body(Body::from(body))
        .unwrap()
}

#[tokio::test]
async fn accepts_browser_form_with_ten_streamed_files_and_all_six_options() {
    let jobs = LocalJobStore::new().unwrap();
    let instructions = "á".repeat(10_000);
    let document = vec![b'x'; 2 * 1024 * 1024 + 32];
    let mut parts = vec![("files", Some("../../study.PDF"), document.as_slice())];
    parts.extend([
        ("language", None, b"en_US".as_slice()),
        ("difficulty", None, b"hard".as_slice()),
        ("quantity", None, b"more".as_slice()),
        ("timeout", None, b"30".as_slice()),
        ("instructions", None, instructions.as_bytes()),
        ("single_cloze", None, b"true".as_slice()),
    ]);
    for _ in 1..10 {
        parts.push(("files", Some("slides.pptx"), b"slides"));
    }
    let response = app(jobs.clone(), MAX_REQUEST_BYTES)
        .oneshot(request(&parts))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let snapshot: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    assert_eq!(snapshot["status"], "queued");
    assert_eq!(snapshot["filenames"][0], "01-study.pdf");
    assert_eq!(snapshot["filenames"][9], "10-slides.pptx");
    assert_eq!(snapshot["discovered_sources"], 0);
    let execution = jobs.wait_next().await.unwrap().unwrap();
    assert_eq!(execution.snapshot().id, snapshot["id"]);
    assert_eq!(execution.options().language(), "en_US");
    assert_eq!(execution.options().difficulty().as_str(), "hard");
    assert_eq!(execution.options().quantity().as_str(), "more");
    assert_eq!(execution.options().timeout(), 30);
    assert_eq!(execution.options().instructions(), instructions);
    assert!(execution.options().single_cloze());
    let input = execution
        .workspace()
        .unwrap()
        .input_dir()
        .join("01-study.pdf");
    assert_eq!(std::fs::read(&input).unwrap(), document);
    drop(execution);
    assert!(!input.exists());
    jobs.close().unwrap();
}

#[tokio::test]
async fn rejects_invalid_forms_body_limits_and_exhausted_queue_without_submitting() {
    let jobs = LocalJobStore::new().unwrap();
    let app = app(jobs.clone(), MAX_REQUEST_BYTES);
    let invalid = vec![
        vec![],
        vec![("files", Some("private.txt"), b"content".as_slice())],
        vec![("files", Some("empty.pdf"), b"".as_slice())],
        vec![("files", None, b"content".as_slice())],
        vec![("language", Some("private.pdf"), b"content".as_slice())],
        vec![("email", None, b"private".as_slice())],
        vec![
            ("language", None, b"en_US".as_slice()),
            ("language", None, b"pt_BR".as_slice()),
        ],
        vec![("quantity", None, b"unlimited".as_slice())],
        vec![("instructions", None, b"\xff".as_slice())],
        vec![("files", Some("study.pdf"), b"data".as_slice()); 11],
    ];
    for parts in invalid {
        let response = app.clone().oneshot(request(&parts)).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let content = to_bytes(response.into_body(), 8192).await.unwrap();
        assert!(!String::from_utf8_lossy(&content).contains("private"));
        assert!(jobs.start_next().unwrap().is_none());
    }
    let long = "á".repeat(10_001);
    let oversized = vec![b'x'; MAX_TEXT_BYTES + 1];
    for value in [long.as_bytes(), oversized.as_slice()] {
        let response = app
            .clone()
            .oneshot(request(&[("instructions", None, value)]))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
    let response = app
        .clone()
        .oneshot(
            HttpRequest::post("/upload")
                .body(Body::from("private"))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let file = [("files", Some("study.pdf"), b"data".as_slice())];
    let response = tests::app(jobs.clone(), 8)
        .oneshot(request(&file))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);
    for _ in 0..3 {
        assert_eq!(
            app.clone().oneshot(request(&file)).await.unwrap().status(),
            StatusCode::ACCEPTED
        );
    }
    assert_eq!(
        app.clone().oneshot(request(&file)).await.unwrap().status(),
        StatusCode::TOO_MANY_REQUESTS
    );
    jobs.close().unwrap();
    assert_eq!(
        app.oneshot(request(&file)).await.unwrap().status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
}

#[tokio::test]
async fn rejects_oversized_filenames_excess_parts_and_unnamed_fields_without_queuing() {
    let jobs = LocalJobStore::new().unwrap();
    let app = app(jobs.clone(), MAX_REQUEST_BYTES);
    let filename = format!("{}.pdf", "x".repeat(4093));
    let mut parts = vec![("files", Some("study.pdf"), b"data".as_slice()); 10];
    parts.extend([
        ("language", None, b"en_US".as_slice()),
        ("difficulty", None, b"hard".as_slice()),
        ("quantity", None, b"more".as_slice()),
        ("timeout", None, b"30".as_slice()),
        ("instructions", None, b"".as_slice()),
        ("single_cloze", None, b"true".as_slice()),
        ("unexpected", None, b"private-form-value".as_slice()),
    ]);
    let unnamed = HttpRequest::post("/upload")
        .header("content-type", "multipart/form-data; boundary=test-boundary")
        .body(Body::from("--test-boundary\r\nContent-Disposition: form-data\r\n\r\nprivate-form-value\r\n--test-boundary--\r\n"))
        .unwrap();
    for request in [
        request(&[("files", Some(&filename), b"data")]),
        request(&parts),
        unnamed,
    ] {
        let response = app.clone().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let content = to_bytes(response.into_body(), 8192).await.unwrap();
        assert!(!String::from_utf8_lossy(&content).contains("private-form-value"));
        assert!(jobs.start_next().unwrap().is_none());
    }
    jobs.close().unwrap();
}

#[tokio::test]
async fn drops_partial_uploads_on_disconnect_or_cancellation_without_queuing() {
    use futures_util::StreamExt;
    use std::{io, task::Poll};
    let jobs = LocalJobStore::new().unwrap();
    let app = app(jobs.clone(), MAX_REQUEST_BYTES);
    let partial = b"--test-boundary\r\nContent-Disposition: form-data; name=\"files\"; filename=\"study.pdf\"\r\n\r\ndata\r\n--test-boundary\r\nContent-Disposition: form-data; name=\"files\"; filename=\"second.pdf\"\r\n\r\n";
    let waiting = Arc::new(tokio::sync::Notify::new());
    let signal = waiting.clone();
    let stream = futures_util::stream::once(async { Ok::<_, io::Error>(partial.to_vec()) }).chain(
        futures_util::stream::poll_fn(move |_| {
            signal.notify_one();
            Poll::Pending::<Option<Result<Vec<u8>, io::Error>>>
        }),
    );
    let body = Body::from_stream(stream);
    let upload = HttpRequest::post("/upload")
        .header(
            "content-type",
            "multipart/form-data; boundary=test-boundary",
        )
        .body(body)
        .unwrap();
    let task = tokio::spawn(app.clone().oneshot(upload));
    tokio::time::timeout(Duration::from_secs(5), waiting.notified())
        .await
        .unwrap();
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    assert!(jobs.start_next().unwrap().is_none());
    let body = Body::from_stream(futures_util::stream::iter([
        Ok(partial.to_vec()),
        Err(io::Error::new(
            io::ErrorKind::ConnectionReset,
            "synthetic disconnect",
        )),
    ]));
    let upload = HttpRequest::post("/upload")
        .header(
            "content-type",
            "multipart/form-data; boundary=test-boundary",
        )
        .body(body)
        .unwrap();
    assert_eq!(
        app.clone().oneshot(upload).await.unwrap().status(),
        StatusCode::BAD_REQUEST
    );
    assert!(jobs.start_next().unwrap().is_none());
    let response = app
        .oneshot(request(&[("files", Some("study.pdf"), b"data")]))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    drop(jobs.start_next().unwrap().unwrap());
    jobs.close().unwrap();
}

#[tokio::test]
async fn rejects_truncated_option_streams_and_body_limits_after_a_completed_file() {
    use std::io;

    let jobs = LocalJobStore::new().unwrap();
    let prefix = b"--test-boundary\r\nContent-Disposition: form-data; name=\"files\"; filename=\"study.pdf\"\r\n\r\ndata\r\n--test-boundary\r\nContent-Disposition: form-data; name=\"instructions\"\r\n\r\nprivate-option-value";
    let interrupted = Body::from_stream(futures_util::stream::iter([
        Ok(prefix.to_vec()),
        Err(io::Error::new(
            io::ErrorKind::ConnectionReset,
            "private-stream-error",
        )),
    ]));
    let oversized = Body::from_stream(futures_util::stream::iter([
        Ok::<_, io::Error>(prefix.to_vec()),
        Ok(vec![b'x'; 1024]),
        Ok(b"\r\n--test-boundary--\r\n".to_vec()),
    ]));
    for (body, limit, expected) in [
        (interrupted, MAX_REQUEST_BYTES, StatusCode::BAD_REQUEST),
        (
            Body::from(prefix.as_slice()),
            MAX_REQUEST_BYTES,
            StatusCode::BAD_REQUEST,
        ),
        (oversized, prefix.len() + 512, StatusCode::PAYLOAD_TOO_LARGE),
    ] {
        let upload = HttpRequest::post("/upload")
            .header(
                "content-type",
                "multipart/form-data; boundary=test-boundary",
            )
            .body(body)
            .unwrap();
        let response = app(jobs.clone(), limit).oneshot(upload).await.unwrap();
        assert_eq!(response.status(), expected);
        let body = to_bytes(response.into_body(), 8192).await.unwrap();
        assert!(!String::from_utf8_lossy(&body).contains("private-"));
        assert!(jobs.start_next().unwrap().is_none());
    }
    let response = app(jobs.clone(), MAX_REQUEST_BYTES)
        .oneshot(request(&[("files", Some("study.pdf"), b"data")]))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    drop(jobs.start_next().unwrap().unwrap());
    jobs.close().unwrap();
}
