use super::*;
mod defaults;
mod failures;
mod fixture;
mod lifecycle;
mod transport;
use crate::{
    deck_exporter::CsvDeckExporter,
    document_preparation::LocalDocumentPreparer,
    notebooklm::{
        RPC_PATH,
        tests::{STORAGE, artifact_row, framed},
    },
};
use axum::{
    Form, Router,
    body::Bytes,
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::{get, post},
};
use flashcards_services::{
    deck_exporter::DeckExporter,
    generation::{self, DEFAULT_INSTRUCTIONS, GenerationError},
    generation_options::GenerationOptions,
    prepared_generation::{PreparedGenerationError, generate_prepared_document},
};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};

#[tokio::test]
async fn cancellation_before_creation_response_still_deletes_the_created_notebook() {
    let created = Arc::new(tokio::sync::Notify::new());
    let release = Arc::new(tokio::sync::Notify::new());
    let deleted = Arc::new(Mutex::new(Vec::<String>::new()));
    let app = Router::new()
        .route(
            "/",
            get(|| async { r#"{"SNlM0e":"csrf","FdrFJe":"session","cfb2h":"build"}"# }),
        )
        .route(RPC_PATH, post(pending_creation_response))
        .with_state(PendingCreation {
            created: created.clone(),
            release: release.clone(),
            deleted: deleted.clone(),
        });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    {
        let creation = client.create_temporary_notebook("Study");
        tokio::pin!(creation);
        tokio::select! {
            _ = &mut creation => panic!("Creation response must remain pending"),
            ready = tokio::time::timeout(Duration::from_secs(5), created.notified()) => ready.unwrap(),
        }
    }
    let mut cleanup = Box::pin(client.finish_cleanup());
    std::future::poll_fn(|context| {
        assert!(std::future::Future::poll(cleanup.as_mut(), context).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
    drop(cleanup);
    let tracked = client.cleanup_tasks.lock().unwrap().len() == 1;
    release.notify_one();
    let complete = client.finish_cleanup().await;
    let removed = deleted.lock().unwrap().clone();
    server.abort();
    let _ = server.await;
    assert!(
        tracked,
        "Canceling the wait must keep pending creation tracked"
    );
    assert!(complete);
    assert_eq!(removed, vec!["owned-pending"]);
}

#[derive(Clone)]
struct PendingCreation {
    created: Arc<tokio::sync::Notify>,
    release: Arc<tokio::sync::Notify>,
    deleted: Arc<Mutex<Vec<String>>>,
}

async fn pending_creation_response(
    State(state): State<PendingCreation>,
    Form(form): Form<BTreeMap<String, String>>,
) -> String {
    let request: Value = serde_json::from_str(&form["f.req"]).unwrap();
    let method = request[0][0][0].as_str().unwrap();
    if method == "CCqFvf" {
        state.created.notify_one();
        state.release.notified().await;
        return framed(method, &json!(["Study", [], "owned-pending"]));
    }
    assert_eq!(method, "WWINqb");
    let params: Value = serde_json::from_str(request[0][0][1].as_str().unwrap()).unwrap();
    state
        .deleted
        .lock()
        .unwrap()
        .push(params[0][0].as_str().unwrap().to_owned());
    framed(method, &Value::Null)
}

#[tokio::test]
async fn generates_exports_and_cleans_owned_notebooks_on_success_failure_and_cancellation() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let sequence = Arc::new(AtomicUsize::new(0));
    let mode = Arc::new(AtomicUsize::new(0));
    let attempts = Arc::new(AtomicUsize::new(0));
    let titles = Arc::new(Mutex::new(BTreeMap::<String, String>::new()));
    let deleted = Arc::new(Mutex::new(Vec::<String>::new()));
    let registered = Arc::new(Mutex::new(BTreeSet::<String>::new()));
    let blocked = Arc::new(Mutex::new(None::<String>));
    let state = fixture::FixtureState {
        sequence: sequence.clone(),
        mode: mode.clone(),
        attempts: attempts.clone(),
        titles,
        registered,
        deleted: deleted.clone(),
        deleted_changed: Arc::new(tokio::sync::Notify::new()),
        blocked: blocked.clone(),
        pending_seen: Arc::new(tokio::sync::Notify::new()),
        calls: Arc::new(Mutex::new(Vec::new())),
        origin: origin.clone(),
    };
    let app = fixture::router(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    check_transient_errors(&client);
    let scenario = Scenario {
        client,
        state,
        temporary: tempfile::tempdir().unwrap(),
        options: serde_json::from_value(json!({"single_cloze":true})).unwrap(),
    };
    check_direct_success(&scenario).await;
    check_direct_failures(&scenario).await;
    check_prepared_chunks(&scenario).await;
    defaults::check_retry_policy(&scenario).await;
    check_retry_matrix(&scenario).await;
    check_retry_cancellation(&scenario).await;
    check_progress_and_formats(&scenario).await;
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

struct Scenario {
    client: NotebookLMClient,
    state: fixture::FixtureState,
    temporary: tempfile::TempDir,
    options: GenerationOptions,
}

fn check_transient_errors(client: &NotebookLMClient) {
    for (error, transient) in [
        (NotebookLMError::Status(StatusCode::TOO_MANY_REQUESTS), true),
        (
            NotebookLMError::Status(StatusCode::SERVICE_UNAVAILABLE),
            true,
        ),
        (NotebookLMError::Status(StatusCode::UNAUTHORIZED), false),
        (NotebookLMError::Status(StatusCode::FORBIDDEN), false),
        (NotebookLMError::Authentication, false),
        (NotebookLMError::Schema("fixture"), false),
        (NotebookLMError::RpcFailure, false),
        (NotebookLMError::SourceFailed, true),
        (NotebookLMError::GenerationFailed, true),
        (
            NotebookLMError::Upload {
                cause: Box::new(NotebookLMError::GenerationFailed),
                cleanup_failed: false,
            },
            true,
        ),
        (
            NotebookLMError::Upload {
                cause: Box::new(NotebookLMError::GenerationFailed),
                cleanup_failed: true,
            },
            false,
        ),
    ] {
        assert_eq!(client.is_transient_error(&error), transient);
    }
}

async fn check_direct_success(scenario: &Scenario) {
    let client = scenario.client.clone();
    let temporary = &scenario.temporary;
    let deleted = &scenario.state.deleted;
    let path = temporary.path().join("part.pdf");
    std::fs::write(&path, b"synthetic-pdf").unwrap();
    let options = scenario.options.clone();
    let result = generation::generate_document(&client, &path, "Study Root", &options)
        .await
        .unwrap();
    assert!(result.cleanup_error.is_none());
    assert!(client.finish_cleanup().await);
    assert_eq!(result.deck.total_cards(), 1);
    assert_eq!(
        result.deck.flashcards[0].front,
        "A {{c1::mitocôndria}} produz ATP celular."
    );
    assert_eq!(result.deck.flashcards[0].tags, ["study_root"]);
    let output = temporary.path().join("cards.csv");
    CsvDeckExporter.export_csv(&result.deck, &output).unwrap();
    assert_eq!(
        std::fs::read_to_string(output).unwrap(),
        "\"A {{c1::mitocôndria}} produz ATP celular.\",\"Energia para células\"\r\n"
    );
    assert!(deleted.lock().unwrap().contains(&"owned-0".to_owned()));
}

async fn check_direct_failures(scenario: &Scenario) {
    let client = scenario.client.clone();
    let path = scenario.temporary.path().join("part.pdf");
    let options = scenario.options.clone();
    let fixture::FixtureState {
        mode,
        deleted,
        sequence,
        ..
    } = &scenario.state;
    mode.store(1, Ordering::SeqCst);
    assert!(matches!(
        generation::generate_document(&client, &path, "Study Root", &options).await,
        Err(GenerationError::Provider(NotebookLMError::Status(
            StatusCode::BAD_REQUEST
        )))
    ));
    assert!(deleted.lock().unwrap().contains(&"owned-1".to_owned()));
    mode.store(2, Ordering::SeqCst);
    let result = generation::generate_document(&client, &path, "Study Root", &options)
        .await
        .unwrap();
    assert_eq!(
        result.deck.total_cards(),
        1,
        "A cleanup failure must not discard generated cards"
    );
    assert!(result.cleanup_error.is_some());
    assert!(!client.finish_cleanup().await);
    mode.store(3, Ordering::SeqCst);
    {
        let pending = scenario.state.pending_seen.notified();
        let generation = generation::generate_document(&client, &path, "Study Root", &options);
        tokio::pin!(generation);
        tokio::select! {
            result = &mut generation => panic!("Generation must remain pending: {:?}", result.err()),
            ready = tokio::time::timeout(Duration::from_secs(5), pending) => ready.unwrap(),
        }
    }
    assert!(client.finish_cleanup().await);
    tokio::time::timeout(
        Duration::from_secs(5),
        wait_for_deleted(&scenario.state, "owned-3"),
    )
    .await
    .unwrap();
    let before = sequence.load(Ordering::SeqCst);
    assert!(matches!(
        generation::generate_document(&client, Path::new("invalid.txt"), "Study Root", &options)
            .await,
        Err(GenerationError::InvalidInput)
    ));
    assert_eq!(sequence.load(Ordering::SeqCst), before);
    assert_eq!(std::fs::read(path).unwrap(), b"synthetic-pdf");
}

async fn wait_for_deleted(state: &fixture::FixtureState, notebook: &str) {
    loop {
        let changed = state.deleted_changed.notified();
        if state
            .deleted
            .lock()
            .unwrap()
            .iter()
            .any(|id| id == notebook)
        {
            return;
        }
        changed.await;
    }
}

async fn check_prepared_chunks(scenario: &Scenario) {
    let client = scenario.client.clone();
    let options = scenario.options.clone();
    let temporary = &scenario.temporary;
    let fixture::FixtureState {
        mode,
        deleted,
        sequence,
        ..
    } = &scenario.state;
    mode.store(0, Ordering::SeqCst);
    let preparer = LocalDocumentPreparer::default();
    let pdf = temporary.path().join("Chapters.pdf");
    let original = include_bytes!("../../../tests/fixtures/chapters.pdf");
    std::fs::write(&pdf, original).unwrap();
    let before = sequence.load(Ordering::SeqCst);
    let result = generate_prepared_document(&preparer, &client, &pdf, "Study Root", &options)
        .await
        .unwrap();
    assert_eq!(sequence.load(Ordering::SeqCst), before + 3);
    assert_eq!(result.deck.total_cards(), 1);
    assert_eq!(result.deck.description, "Deck de Study Root (3 chunks)");
    assert_eq!(result.deck.notebook_id, String::new());
    assert!(result.cleanup_errors.is_empty());
    assert_eq!(result.deck.flashcards[0].tags, ["study_root"]);
    for index in before..before + 3 {
        assert!(deleted.lock().unwrap().contains(&format!("owned-{index}")));
    }
    let before = sequence.load(Ordering::SeqCst);
    mode.store(100 + before + 1, Ordering::SeqCst);
    let failed = generate_prepared_document(&preparer, &client, &pdf, "Study Root", &options).await;
    match failed {
        Err(PreparedGenerationError::Generation {
            completed,
            failed_chunk,
            ..
        }) => {
            assert_eq!(failed_chunk, 2);
            assert_eq!(completed.len(), 1);
            assert_eq!(completed[0].deck.total_cards(), 2);
            assert_eq!(completed[0].deck.name, "Study Root_chunk1");
            assert_eq!(completed[0].deck.description, "Chunk 1 of 3");
        }
        _ => panic!("Expected partial results from a failed second chunk"),
    }
    assert_eq!(sequence.load(Ordering::SeqCst), before + 2);
    assert!(
        deleted
            .lock()
            .unwrap()
            .contains(&format!("owned-{}", before + 1))
    );
    assert_eq!(std::fs::read(&pdf).unwrap(), original);
}

async fn check_retry_matrix(scenario: &Scenario) {
    let client = scenario.client.clone();
    let options = scenario.options.clone();
    let temporary = &scenario.temporary;
    let preparer = LocalDocumentPreparer::default();
    let pdf = temporary.path().join("Chapters.pdf");
    let fixture::FixtureState {
        mode,
        deleted,
        sequence,
        attempts,
        ..
    } = &scenario.state;
    for selected in [7, 8, 9, 10] {
        mode.store(selected, Ordering::SeqCst);
        attempts.store(0, Ordering::SeqCst);
        let before = sequence.load(Ordering::SeqCst);
        let result =
            generate_prepared_document(&preparer, &client, &pdf, "Study Root", &options).await;
        let expected_attempts = if selected == 7 {
            assert_eq!(result.unwrap().deck.total_cards(), 1);
            5
        } else if selected == 8 {
            assert!(matches!(result, Err(PreparedGenerationError::Generation {
                completed, failed_chunk: 1,
                error: GenerationError::Provider(NotebookLMError::Status(StatusCode::TOO_MANY_REQUESTS)),
            }) if completed.is_empty()));
            3
        } else if selected == 9 {
            assert!(matches!(result, Err(PreparedGenerationError::Generation {
                completed, failed_chunk: 1,
                error: GenerationError::Creation(NotebookLMError::Status(StatusCode::SERVICE_UNAVAILABLE)),
            }) if completed.is_empty()));
            1
        } else {
            assert!(matches!(result, Err(PreparedGenerationError::Generation {
                completed, failed_chunk: 1, error: GenerationError::Cleanup { .. },
            }) if completed.is_empty()));
            1
        };
        assert_eq!(attempts.load(Ordering::SeqCst), expected_attempts);
        let created = if selected == 9 { 0 } else { expected_attempts };
        assert_eq!(sequence.load(Ordering::SeqCst), before + created);
        assert_eq!(client.finish_cleanup().await, selected != 10);
        for index in before..before + created {
            assert!(deleted.lock().unwrap().contains(&format!("owned-{index}")));
        }
    }
}

async fn check_retry_cancellation(scenario: &Scenario) {
    let client = scenario.client.clone();
    let options = scenario.options.clone();
    let preparer = LocalDocumentPreparer::default();
    let pdf = scenario.temporary.path().join("Chapters.pdf");
    let fixture::FixtureState {
        mode,
        sequence,
        attempts,
        ..
    } = &scenario.state;
    mode.store(8, Ordering::SeqCst);
    attempts.store(0, Ordering::SeqCst);
    let before = sequence.load(Ordering::SeqCst);
    let notebook = format!("owned-{before}");
    {
        let generation =
            generate_prepared_document(&preparer, &client, &pdf, "Study Root", &options);
        tokio::pin!(generation);
        tokio::select! {
            result = &mut generation => panic!("First failure must wait before retry: {:?}", result.err()),
            ready = tokio::time::timeout(Duration::from_secs(30), wait_for_deleted(&scenario.state, &notebook)) => ready.unwrap(),
        }
    }
    assert!(client.finish_cleanup().await);
    assert_eq!(attempts.load(Ordering::SeqCst), 1);
    assert_eq!(sequence.load(Ordering::SeqCst), before + 1);
}

async fn check_progress_and_formats(scenario: &Scenario) {
    let client = scenario.client.clone();
    let options = scenario.options.clone();
    let temporary = &scenario.temporary;
    let preparer = LocalDocumentPreparer::default();
    let pdf = temporary.path().join("Chapters.pdf");
    let mode = &scenario.state.mode;
    mode.store(2, Ordering::SeqCst);
    let result = generate_prepared_document(&preparer, &client, &pdf, "Study Root", &options)
        .await
        .unwrap();
    assert_eq!(result.cleanup_errors.len(), 3);
    assert_eq!(result.deck.total_cards(), 1);
    assert!(!client.finish_cleanup().await);
    mode.store(6, Ordering::SeqCst);
    let mut exported_parts = 0;
    let filtered =
        flashcards_services::prepared_generation::generate_prepared_document_with_progress(
            &preparer,
            &client,
            &pdf,
            "Study Root",
            &options,
            &mut |deck, _, _| {
                assert_eq!(deck.total_cards(), 1);
                exported_parts += 1;
            },
        )
        .await;
    assert!(
        matches!(filtered, Err(PreparedGenerationError::NoCards { cleanup_errors }) if cleanup_errors.is_empty())
    );
    assert_eq!(exported_parts, 3);
    let single = generation::generate_document(&client, &pdf, "Study Root", &options)
        .await
        .unwrap();
    assert_eq!(single.deck.total_cards(), 1);
    assert!(client.finish_cleanup().await);
    mode.store(0, Ordering::SeqCst);
    let pptx = temporary.path().join("Slides.PPTX");
    let original = include_bytes!("../../../tests/fixtures/minimal.pptx");
    std::fs::write(&pptx, original).unwrap();
    let result = generate_prepared_document(&preparer, &client, &pptx, "Study Root", &options)
        .await
        .unwrap();
    assert_eq!(result.deck.total_cards(), 1);
    assert_eq!(result.deck.description, "Deck de Study Root");
    assert_ne!(result.deck.notebook_id, String::new());
    assert!(result.cleanup_errors.is_empty());
    assert_eq!(std::fs::read(&pptx).unwrap(), original);
    mode.store(5, Ordering::SeqCst);
    assert!(
        matches!(generate_prepared_document(&preparer, &client, &pdf, "Study Root", &options).await, Err(PreparedGenerationError::NoCards { cleanup_errors }) if cleanup_errors.is_empty())
    );
}
