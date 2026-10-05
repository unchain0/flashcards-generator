use super::*;
use flashcards_services::{
    document_export::{DocumentBatch, DocumentExportResult, ExportIssue, generate_batch_csv},
    document_inputs::{SourceFilename, safe_filenames},
    local_jobs::{JobStatus, SourceOutcome},
};
use std::error::Error;

#[tokio::test]
async fn failed_processing_stops_later_requests_and_cleans_only_the_owned_notebook() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    let app = fixture::router(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("part.pdf");
    std::fs::write(&source, b"synthetic-pdf").unwrap();
    let source_failure = ["CCqFvf", "rLM1Ne", "o4cbdc", "rLM1Ne", "WWINqb"];
    let artifact_failure = [
        "CCqFvf", "rLM1Ne", "o4cbdc", "rLM1Ne", "R7cb6c", "gArtLc", "WWINqb",
    ];
    for (mode, expected) in [
        (11, source_failure.as_slice()),
        (12, artifact_failure.as_slice()),
        (13, source_failure.as_slice()),
        (14, artifact_failure.as_slice()),
    ] {
        state.mode.store(mode, Ordering::SeqCst);
        state.attempts.store(0, Ordering::SeqCst);
        state.calls.lock().unwrap().clear();
        let notebook = state.sequence.load(Ordering::SeqCst);
        let result = generation::generate_document(
            &client,
            &source,
            "Study Root",
            &GenerationOptions::default(),
        )
        .await;
        assert!(
            matches!(
                (mode, &result),
                (
                    11,
                    Err(GenerationError::Provider(NotebookLMError::SourceFailed))
                ) | (
                    12,
                    Err(GenerationError::Provider(NotebookLMError::GenerationFailed))
                ) | (
                    13 | 14,
                    Err(GenerationError::Provider(NotebookLMError::Status(
                        StatusCode::BAD_GATEWAY
                    ))),
                )
            ),
            "Unexpected processing outcome: {result:?}"
        );
        assert!(client.finish_cleanup().await);
        assert_eq!(*state.calls.lock().unwrap(), expected);
        assert_eq!(
            state.attempts.load(Ordering::SeqCst),
            usize::from(matches!(mode, 12 | 14))
        );
        let deleted = state.deleted.lock().unwrap();
        assert_eq!(deleted.last().unwrap(), &format!("owned-{notebook}"));
        assert_eq!(deleted.len(), notebook + 1);
        assert_eq!(std::fs::read(&source).unwrap(), b"synthetic-pdf");
    }
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

struct SinglePreparedSource(Vec<std::path::PathBuf>);

#[tokio::test]
async fn empty_final_decks_report_every_cleanup_failure_and_preserve_partial_csvs() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    let server = tokio::spawn(axum::serve(listener, fixture::router(state.clone())).into_future());
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let directory = tempfile::tempdir().unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    let source = directory.path().join(names[0].as_str());
    let original = include_bytes!("../../../../tests/fixtures/chapters.pdf");
    std::fs::write(&source, original).unwrap();
    for mode in [16, 17] {
        state.mode.store(mode, Ordering::SeqCst);
        let before = state.sequence.load(Ordering::SeqCst);
        let output = directory.path().join(format!("empty-output-{mode}"));
        std::fs::create_dir(&output).unwrap();
        std::fs::write(output.join("01-study.csv"), b"preserve existing final CSV").unwrap();
        check_empty_final_deck_case(
            &client,
            &state,
            DocumentBatch {
                input_dir: directory.path(),
                output_dir: &output,
                filenames: &names,
                options: &GenerationOptions::default(),
            },
            mode,
            before,
        )
        .await;
        assert_eq!(std::fs::read(&source).unwrap(), original);
    }
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn check_empty_final_deck_case(
    client: &NotebookLMClient,
    state: &fixture::FixtureState,
    batch: DocumentBatch<'_>,
    mode: usize,
    before: usize,
) {
    let output = batch.output_dir;
    let mut reports = Vec::new();
    let status = generate_batch_csv(
        &LocalDocumentPreparer::default(),
        client,
        &CsvDeckExporter,
        batch,
        |result| {
            reports.push(result);
            Ok::<_, std::convert::Infallible>(())
        },
    )
    .await
    .unwrap();
    assert_eq!(status, JobStatus::Failed);
    assert_eq!(reports.len(), 1);
    assert!(!client.finish_cleanup().await);
    assert_eq!(state.sequence.load(Ordering::SeqCst), before + 3);
    for index in before..before + 3 {
        assert!(
            state
                .deleted
                .lock()
                .unwrap()
                .contains(&format!("owned-{index}"))
        );
    }
    check_empty_final_deck_report(&reports[0], output, mode);
}

#[tokio::test]
async fn failed_generation_reports_provider_and_cleanup_causes_separately() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    state.mode.store(10, Ordering::SeqCst);
    let server = tokio::spawn(axum::serve(listener, fixture::router(state.clone())).into_future());
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let directory = tempfile::tempdir().unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    let source = directory.path().join(names[0].as_str());
    std::fs::write(&source, b"synthetic-pdf").unwrap();
    let result = flashcards_services::document_export::generate_document_csv(
        &PassThroughPreparer,
        &client,
        &CsvDeckExporter,
        directory.path(),
        directory.path(),
        &names[0],
        &GenerationOptions::default(),
    )
    .await;
    assert_eq!(result.outcome, SourceOutcome::Failed);
    assert_eq!(result.artifacts, Vec::<String>::new());
    assert!(!client.finish_cleanup().await);
    assert_eq!(state.sequence.load(Ordering::SeqCst), 1);
    assert_eq!(state.attempts.load(Ordering::SeqCst), 1);
    assert_eq!(result.issues.len(), 2);
    assert!(
        matches!(&result.issues[0], ExportIssue::Generation(PreparedGenerationError::Generation { completed, failed_chunk: 1, error: GenerationError::Provider(NotebookLMError::Status(StatusCode::TOO_MANY_REQUESTS)) }) if completed.is_empty())
    );
    assert!(matches!(
        &result.issues[1],
        ExportIssue::Cleanup(NotebookLMError::Status(StatusCode::INTERNAL_SERVER_ERROR))
    ));
    assert!(
        result
            .issues
            .iter()
            .all(|issue| !issue.to_string().contains("study.pdf"))
    );
    assert_eq!(std::fs::read(&source).unwrap(), b"synthetic-pdf");
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

fn check_empty_final_deck_report(
    result: &DocumentExportResult<
        crate::document_preparation::PreparationError,
        NotebookLMError,
        std::io::Error,
    >,
    output: &Path,
    mode: usize,
) {
    assert_eq!(result.outcome, SourceOutcome::Failed);
    assert_eq!(result.issues.len(), 4);
    assert!(
        matches!(&result.issues[0], ExportIssue::Generation(PreparedGenerationError::NoCards { cleanup_errors }) if cleanup_errors.is_empty())
    );
    for issue in &result.issues[1..] {
        assert!(matches!(
            issue,
            ExportIssue::Cleanup(NotebookLMError::Status(StatusCode::INTERNAL_SERVER_ERROR))
        ));
        assert!(matches!(
            issue
                .source()
                .unwrap()
                .downcast_ref::<NotebookLMError>()
                .unwrap(),
            NotebookLMError::Status(StatusCode::INTERNAL_SERVER_ERROR)
        ));
        assert_eq!(issue.to_string(), "Temporary notebook cleanup failed");
    }
    let expected: Vec<_> = if mode == 17 {
        (1..=3)
            .map(|index| format!("01-study_chunk{index}.csv"))
            .collect()
    } else {
        Vec::new()
    };
    assert_eq!(result.artifacts, expected);
    for name in &expected {
        assert_eq!(
            std::fs::read(output.join(name)).unwrap(),
            "\"A {{c1::mitocôndria}} produz ATP celular.\",\"Energia\"\r\n".as_bytes()
        );
    }
    assert_eq!(
        std::fs::read(output.join("01-study.csv")).unwrap(),
        b"preserve existing final CSV"
    );
    assert_eq!(
        std::fs::read_dir(output).unwrap().count(),
        1 + expected.len()
    );
}

#[tokio::test]
async fn preparation_failures_preserve_existing_exports_and_never_create_notebooks() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    let server = tokio::spawn(axum::serve(listener, fixture::router(state.clone())).into_future());
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let directory = tempfile::tempdir().unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    let output = directory.path().join("01-study.csv");
    std::fs::write(&output, b"preserve original CSV").unwrap();
    check_preparation_failure_report(&client, directory.path(), &names[0], true).await;
    let source = directory.path().join(names[0].as_str());
    assert!(!source.exists());
    std::fs::write(&source, b"invalid PDF bytes").unwrap();
    check_preparation_failure_report(&client, directory.path(), &names[0], false).await;
    assert_eq!(std::fs::read(&source).unwrap(), b"invalid PDF bytes");
    assert_eq!(std::fs::read(&output).unwrap(), b"preserve original CSV");
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 2);
    assert_eq!(state.sequence.load(Ordering::SeqCst), 0);
    assert_eq!(*state.calls.lock().unwrap(), Vec::<String>::new());
    assert!(client.finish_cleanup().await);
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn check_preparation_failure_report(
    client: &NotebookLMClient,
    root: &Path,
    filename: &SourceFilename,
    missing: bool,
) {
    use crate::{document_preparation::PreparationError, pdf::PdfError};
    let result = flashcards_services::document_export::generate_document_csv(
        &LocalDocumentPreparer::default(),
        client,
        &CsvDeckExporter,
        root,
        root,
        filename,
        &GenerationOptions::default(),
    )
    .await;
    assert_eq!(result.outcome, SourceOutcome::Failed);
    assert_eq!(result.artifacts, Vec::<String>::new());
    assert_eq!(result.issues.len(), 1);
    assert!(matches!(
        &result.issues[0],
        ExportIssue::Generation(PreparedGenerationError::Preparation(PreparationError::Pdf(
            PdfError::Io(_) | PdfError::InvalidInput
        )))
    ));
    assert_eq!(
        matches!(
            &result.issues[0],
            ExportIssue::Generation(PreparedGenerationError::Preparation(PreparationError::Pdf(
                PdfError::Io(_)
            )))
        ),
        missing
    );
    assert_eq!(result.issues[0].to_string(), "Document generation failed");
    assert!(
        result.issues[0]
            .source()
            .unwrap()
            .source()
            .unwrap()
            .source()
            .unwrap()
            .downcast_ref::<PdfError>()
            .is_some()
    );
}

impl flashcards_services::document_preparation::PreparedDocuments for SinglePreparedSource {
    fn files(&self) -> &[std::path::PathBuf] {
        &self.0
    }
}
struct PassThroughPreparer;
impl flashcards_services::document_preparation::DocumentPreparer for PassThroughPreparer {
    type Error = std::io::Error;
    type Prepared = SinglePreparedSource;
    fn prepare(
        &self,
        source: &Path,
    ) -> impl std::future::Future<Output = Result<Self::Prepared, Self::Error>> + Send {
        std::future::ready(Ok(SinglePreparedSource(vec![source.to_owned()])))
    }
}

struct ReportingExporter {
    fail: bool,
}

struct PartialExporter<'a> {
    mode: &'a AtomicUsize,
    fail_next: usize,
}

impl flashcards_services::deck_exporter::DeckExporter for PartialExporter<'_> {
    type Error = std::io::Error;

    fn export_csv(&self, deck: &flashcards_domain::Deck, path: &Path) -> Result<(), Self::Error> {
        CsvDeckExporter.export_csv(deck, path)?;
        self.mode.store(self.fail_next, Ordering::SeqCst);
        Ok(())
    }
}

#[tokio::test]
async fn later_chunk_failure_keeps_exported_cards_and_prior_cleanup_errors() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    let server = tokio::spawn(axum::serve(listener, fixture::router(state.clone())).into_future());
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let directory = tempfile::tempdir().unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    let source = directory.path().join(names[0].as_str());
    let original = include_bytes!("../../../../tests/fixtures/chapters.pdf");
    std::fs::write(&source, original).unwrap();
    for mode in [0, 2] {
        let output = directory.path().join(format!("output-{mode}"));
        std::fs::create_dir(&output).unwrap();
        check_partial_chunk_failure(
            &client,
            &state,
            DocumentBatch {
                input_dir: directory.path(),
                output_dir: &output,
                filenames: &names,
                options: &GenerationOptions::default(),
            },
            mode,
        )
        .await;
        assert_eq!(std::fs::read(&source).unwrap(), original);
    }
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn check_partial_chunk_failure(
    client: &NotebookLMClient,
    state: &fixture::FixtureState,
    batch: DocumentBatch<'_>,
    mode: usize,
) {
    state.mode.store(mode, Ordering::SeqCst);
    let before = state.sequence.load(Ordering::SeqCst);
    let exporter = PartialExporter {
        mode: &state.mode,
        fail_next: 100 + before + 1,
    };
    let output = batch.output_dir;
    let mut reports = Vec::new();
    let status = generate_batch_csv(
        &LocalDocumentPreparer::default(),
        client,
        &exporter,
        batch,
        |result| {
            reports.push(result);
            Ok::<_, std::convert::Infallible>(())
        },
    )
    .await
    .unwrap();
    assert_eq!(status, JobStatus::Failed);
    assert_eq!(reports.len(), 1);
    let result = &reports[0];
    assert_eq!(result.outcome, SourceOutcome::Failed);
    assert_eq!(result.artifacts, ["01-study_chunk1.csv"]);
    assert_eq!(result.issues.len(), 1 + usize::from(mode == 2));
    assert!(matches!(
        &result.issues[0],
        ExportIssue::Generation(PreparedGenerationError::Generation {
            completed,
            failed_chunk: 2,
            error: GenerationError::Provider(NotebookLMError::Status(StatusCode::BAD_REQUEST)),
        }) if completed.is_empty()
    ));
    assert_eq!(
        result
            .issues
            .iter()
            .filter(|issue| matches!(
                issue,
                ExportIssue::Cleanup(NotebookLMError::Status(StatusCode::INTERNAL_SERVER_ERROR))
            ))
            .count(),
        usize::from(mode == 2)
    );
    assert_ne!(
        std::fs::read(output.join(&result.artifacts[0])).unwrap(),
        [] as [u8; 0]
    );
    assert_eq!(std::fs::read_dir(output).unwrap().count(), 1);
    assert_eq!(state.sequence.load(Ordering::SeqCst), before + 2);
    assert!(client.finish_cleanup().await);
    for index in before..before + 2 {
        assert!(
            state
                .deleted
                .lock()
                .unwrap()
                .contains(&format!("owned-{index}"))
        );
    }
}

impl flashcards_services::deck_exporter::DeckExporter for ReportingExporter {
    type Error = std::io::Error;
    fn export_csv(&self, deck: &flashcards_domain::Deck, path: &Path) -> Result<(), Self::Error> {
        if self.fail {
            return Err(std::io::Error::other("synthetic-private-export-path"));
        }
        CsvDeckExporter.export_csv(deck, path)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum BatchOutcome {
    Completed,
    SourceFailed,
    ExportFailed,
}

#[tokio::test]
async fn batch_reporting_preserves_exports_and_stops_only_when_the_consumer_fails() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    let app = fixture::router(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let directory = tempfile::tempdir().unwrap();
    let names = safe_filenames(&["first.pdf".into(), "second.pdf".into()]).unwrap();
    for name in &names {
        std::fs::write(directory.path().join(name.as_str()), b"synthetic-pdf").unwrap();
    }
    for (case, report_error, mode, instructions, expected) in [
        (
            "disconnected",
            Some("report consumer disconnected"),
            0,
            "",
            BatchOutcome::Completed,
        ),
        (
            "connected",
            None,
            15,
            fixture::CUSTOM_INSTRUCTIONS,
            BatchOutcome::Completed,
        ),
        ("source-failed", None, 11, "", BatchOutcome::SourceFailed),
        ("export-failed", None, 0, "", BatchOutcome::ExportFailed),
    ] {
        let output = directory.path().join(case);
        std::fs::create_dir(&output).unwrap();
        state.mode.store(mode, Ordering::SeqCst);
        let options = serde_json::from_value(json!({"instructions": instructions})).unwrap();
        check_batch_reporting(
            &client,
            &state,
            DocumentBatch {
                input_dir: directory.path(),
                output_dir: &output,
                filenames: &names,
                options: &options,
            },
            report_error,
            expected,
        )
        .await;
    }
    for name in &names {
        assert_eq!(
            std::fs::read(directory.path().join(name.as_str())).unwrap(),
            b"synthetic-pdf"
        );
    }
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn check_batch_reporting(
    client: &NotebookLMClient,
    state: &fixture::FixtureState,
    batch: DocumentBatch<'_>,
    report_error: Option<&str>,
    expected: BatchOutcome,
) {
    let names = batch.filenames;
    let output = batch.output_dir;
    let expected_count = report_error.map_or(2, |_| 1);
    let first_notebook = state.sequence.load(Ordering::SeqCst);
    let mut reported = 0;
    let exporter = ReportingExporter {
        fail: expected == BatchOutcome::ExportFailed,
    };
    let result = generate_batch_csv(&PassThroughPreparer, client, &exporter, batch, |result| {
        check_source_report(&result, names[reported].stem(), expected);
        reported += 1;
        report_error.map_or(Ok(()), Err)
    })
    .await;
    let status = if expected == BatchOutcome::Completed {
        JobStatus::Completed
    } else {
        JobStatus::Failed
    };
    assert_eq!(result, report_error.map_or(Ok(status), Err));
    assert_eq!(reported, expected_count);
    assert!(client.finish_cleanup().await);
    assert_eq!(
        state.sequence.load(Ordering::SeqCst),
        first_notebook + expected_count
    );
    let expected_deleted: Vec<_> = (0..first_notebook + expected_count)
        .map(|index| format!("owned-{index}"))
        .collect();
    assert_eq!(*state.deleted.lock().unwrap(), expected_deleted);
    let artifacts = if expected == BatchOutcome::Completed {
        expected_count
    } else {
        0
    };
    assert_eq!(std::fs::read_dir(output).unwrap().count(), artifacts);
    for name in names.iter().take(artifacts) {
        let csv = std::fs::read_to_string(output.join(format!("{}.csv", name.stem()))).unwrap();
        assert_eq!(
            csv,
            "\"A {{c2::mitocôndria}} produz ATP celular.\",\"Energia para células\"\r\n"
        );
    }
}

fn check_source_report(
    result: &DocumentExportResult<std::io::Error, NotebookLMError, std::io::Error>,
    stem: &str,
    expected: BatchOutcome,
) {
    if expected == BatchOutcome::Completed {
        assert_eq!(result.outcome, SourceOutcome::Completed);
        assert_eq!(result.artifacts, [format!("{stem}.csv")]);
        assert!(result.issues.is_empty());
        return;
    }
    assert_eq!(result.outcome, SourceOutcome::Failed);
    assert_eq!(result.artifacts.len(), 0);
    assert_eq!(result.issues.len(), 1);
    let issue = &result.issues[0];
    assert!(!issue.to_string().contains("synthetic-private"));
    match expected {
        BatchOutcome::SourceFailed => {
            assert!(matches!(
                issue,
                ExportIssue::Generation(PreparedGenerationError::Generation {
                    failed_chunk: 1,
                    error: GenerationError::Provider(NotebookLMError::SourceFailed),
                    ..
                })
            ));
            assert!(issue.source().unwrap().source().is_some());
        }
        BatchOutcome::ExportFailed => {
            let cause = issue
                .source()
                .unwrap()
                .downcast_ref::<std::io::Error>()
                .unwrap();
            assert_eq!(cause.to_string(), "synthetic-private-export-path");
        }
        BatchOutcome::Completed => panic!("Completed sources must have returned above"),
    }
}

struct FailingPreparer(AtomicUsize);
impl flashcards_services::document_preparation::DocumentPreparer for FailingPreparer {
    type Error = std::io::Error;
    type Prepared = SinglePreparedSource;
    fn prepare(
        &self,
        _source: &Path,
    ) -> impl std::future::Future<Output = Result<Self::Prepared, Self::Error>> + Send {
        self.0.fetch_add(1, Ordering::SeqCst);
        std::future::ready(Err(std::io::Error::other("synthetic-private-preparation")))
    }
}

#[tokio::test]
async fn foreign_store_execution_never_prepares_documents_or_creates_notebooks() {
    use crate::local_job_store::{LocalJobStore, StoreError};
    use flashcards_domain::identity::UserId;
    use flashcards_services::local_job_registry::RegistryError;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    let app = fixture::router(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let foreign = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"synthetic-pdf")
        .unwrap();
    let snapshot = reservation.submit().unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let preparer = FailingPreparer(AtomicUsize::new(0));
    let result = crate::local_generation::execute_job(
        &foreign,
        execution,
        &preparer,
        &client,
        &CsvDeckExporter,
    )
    .await;
    assert!(matches!(
        result,
        Err(StoreError::Registry(RegistryError::Missing))
    ));
    assert_eq!(preparer.0.load(Ordering::SeqCst), 0);
    assert_eq!(state.sequence.load(Ordering::SeqCst), 0);
    assert_eq!(state.calls.lock().unwrap().len(), 0);
    assert_eq!(
        jobs.snapshot(&owner, &snapshot.id).unwrap().unwrap().status,
        JobStatus::Cancelled
    );
    jobs.close().unwrap();
    foreign.close().unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

#[tokio::test]
async fn document_validation_precedes_provider_requests_and_counts_unicode_characters() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    let app = fixture::router(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("part.PDF");
    std::fs::write(&source, b"synthetic-pdf").unwrap();
    let options = GenerationOptions::default();
    for (path, name) in [
        (source.clone(), String::new()),
        (source.clone(), " \t ".into()),
        (source.clone(), "á".repeat(1001)),
        (directory.path().join("part.txt"), "Study".into()),
        (directory.path().join("part"), "Study".into()),
    ] {
        assert!(matches!(
            generation::generate_document(&client, &path, &name, &options).await,
            Err(GenerationError::InvalidInput)
        ));
    }
    assert_eq!(state.sequence.load(Ordering::SeqCst), 0);
    assert_eq!(state.calls.lock().unwrap().len(), 0);
    assert_eq!(std::fs::read(&source).unwrap(), b"synthetic-pdf");
    let name = "á".repeat(1000);
    let result = generation::generate_document(&client, &source, &name, &options)
        .await
        .unwrap();
    assert_eq!(result.deck.name, name);
    assert_eq!(result.deck.total_cards(), 1);
    assert!(result.cleanup_error.is_none());
    assert!(client.finish_cleanup().await);
    assert_eq!(state.sequence.load(Ordering::SeqCst), 1);
    assert_eq!(*state.deleted.lock().unwrap(), ["owned-0"]);
    assert_eq!(std::fs::read(&source).unwrap(), b"synthetic-pdf");
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}
