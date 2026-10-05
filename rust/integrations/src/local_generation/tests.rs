use super::*;
use crate::deck_exporter::CsvDeckExporter;
use flashcards_domain::{Deck, Flashcard, identity::UserId};
use flashcards_services::{document_inputs::safe_filenames, generation_options::GenerationOptions};

struct UnusedNotebookFactory;

impl TemporaryNotebookFactory for UnusedNotebookFactory {
    type Gateway = crate::notebooklm::NotebookLMClient;
    type Scope<'a> = <Self::Gateway as TemporaryNotebookFactory>::Scope<'a>;

    fn create_temporary_notebook(
        &self,
        _title: &str,
    ) -> impl std::future::Future<
        Output = Result<Self::Scope<'_>, crate::notebooklm::NotebookLMError>,
    > + Send {
        std::future::poll_fn(|_| panic!("Invalid job state must not request a notebook"))
    }
}

#[tokio::test]
async fn duplicate_source_reporting_fails_the_job_without_losing_registered_csvs() {
    use flashcards_services::{local_job_registry::RegistryError, local_jobs::SourceOutcome};
    use std::io::{Read, Write};
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"synthetic invalid PDF")
        .unwrap();
    let id = reservation.submit().unwrap().id;
    let execution = jobs.start_next().unwrap().unwrap();
    let input = {
        let mut workspace = execution.workspace().unwrap();
        std::fs::write(
            workspace.output_dir().join("partial.csv"),
            b"preserved,answer\r\n",
        )
        .unwrap();
        workspace.register_artifact("partial.csv").unwrap();
        workspace.input_dir().join(names[0].as_str())
    };
    assert_eq!(std::fs::read(&input).unwrap(), b"synthetic invalid PDF");
    jobs.record_source(&execution, SourceOutcome::Skipped)
        .unwrap();
    let error = execute_job(
        &jobs,
        execution,
        &crate::document_preparation::LocalDocumentPreparer::default(),
        &UnusedNotebookFactory,
        &CsvDeckExporter,
    )
    .await
    .unwrap_err();
    assert!(matches!(
        error,
        StoreError::Registry(RegistryError::InvalidState)
    ));
    assert!(!error.to_string().contains("synthetic"));
    assert!(
        error
            .source()
            .unwrap()
            .downcast_ref::<RegistryError>()
            .is_some()
    );
    let snapshot = jobs.snapshot(&owner, &id).unwrap().unwrap();
    assert_eq!(snapshot.status, JobStatus::Failed);
    assert_eq!(snapshot.discovered_sources, 1);
    assert_eq!(snapshot.skipped_sources, 1);
    assert_eq!(snapshot.completed_sources, 0);
    assert_eq!(snapshot.failed_sources, 0);
    assert_eq!(snapshot.artifacts, ["partial.csv"]);
    assert!(!input.exists());
    let mut artifact = jobs.artifact(&owner, &id, "partial.csv").unwrap().unwrap();
    let mut contents = Vec::new();
    artifact.read_to_end(&mut contents).unwrap();
    assert_eq!(contents, b"preserved,answer\r\n");
    drop(artifact);
    jobs.close().unwrap();
}

#[tokio::test]
async fn unavailable_workspace_stops_execution_and_is_released_when_storage_closes() {
    use std::io::Write;
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"synthetic original input")
        .unwrap();
    let id = reservation.submit().unwrap().id;
    let execution = jobs.start_next().unwrap().unwrap();
    let input = execution
        .workspace()
        .unwrap()
        .input_dir()
        .join(names[0].as_str());
    assert_eq!(std::fs::read(&input).unwrap(), b"synthetic original input");
    let blocked_output = execution
        .workspace()
        .unwrap()
        .output_dir()
        .join("blocked.csv");
    let poisoned = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _guard = execution.workspace().unwrap();
        panic!("synthetic workspace failure");
    }));
    assert!(poisoned.is_err());
    let publication = RegisteredExporter {
        exporter: &CsvDeckExporter,
        execution: &execution,
    }
    .export_csv(&Deck::new("Study".into()), &blocked_output)
    .unwrap_err();
    assert!(matches!(
        publication,
        RegisteredExportError::Storage(StoreError::Unavailable)
    ));
    assert!(!blocked_output.exists());
    let error = execute_job(
        &jobs,
        execution,
        &crate::document_preparation::LocalDocumentPreparer::default(),
        &UnusedNotebookFactory,
        &CsvDeckExporter,
    )
    .await
    .unwrap_err();
    assert!(matches!(error, StoreError::Unavailable));
    assert_eq!(error.to_string(), "Local job storage unavailable");
    assert!(error.source().is_none());
    let snapshot = jobs.snapshot(&owner, &id).unwrap().unwrap();
    assert_eq!(snapshot.status, JobStatus::Running);
    assert_eq!(snapshot.artifacts, Vec::<String>::new());
    assert_eq!(snapshot.completed_sources, 0);
    assert_eq!(snapshot.skipped_sources, 0);
    assert_eq!(snapshot.failed_sources, 0);
    assert_eq!(std::fs::read(&input).unwrap(), b"synthetic original input");
    jobs.close().unwrap();
    assert!(!input.exists());
}

struct BoundaryExporter {
    calls: std::sync::atomic::AtomicUsize,
    fail: bool,
}

impl DeckExporter for BoundaryExporter {
    type Error = std::io::Error;
    fn export_csv(&self, _deck: &Deck, _path: &Path) -> Result<(), Self::Error> {
        self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        if self.fail {
            Err(std::io::Error::other("synthetic-private-export-path"))
        } else {
            Ok(())
        }
    }
}

#[tokio::test]
async fn rejects_outside_exports_and_missing_publications_with_private_diagnostics() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    jobs.reserve(owner, names, GenerationOptions::default())
        .unwrap()
        .submit()
        .unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let output = execution.workspace().unwrap().output_dir().to_owned();
    let external = tempfile::tempdir().unwrap();
    let original = external.path().join("preserve.csv");
    std::fs::write(&original, b"preserve external export").unwrap();
    let deck = Deck::new("study".into());
    let exporter = BoundaryExporter {
        calls: AtomicUsize::new(0),
        fail: true,
    };
    let registered = RegisteredExporter {
        exporter: &exporter,
        execution: &execution,
    };
    let error = registered.export_csv(&deck, &original).unwrap_err();
    assert!(matches!(
        error,
        RegisteredExportError::Storage(StoreError::Workspace(WorkspaceError::InvalidArtifact))
    ));
    assert_eq!(exporter.calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        std::fs::read(&original).unwrap(),
        b"preserve external export"
    );
    assert!(error.source().is_some());
    let error = registered
        .export_csv(&deck, &output.join("deck.csv"))
        .unwrap_err();
    assert!(matches!(error, RegisteredExportError::Export(_)));
    assert_eq!(exporter.calls.load(Ordering::SeqCst), 1);
    assert!(!error.to_string().contains("synthetic-private-export-path"));
    assert_eq!(
        error.source().unwrap().to_string(),
        "synthetic-private-export-path"
    );
    let exporter = BoundaryExporter {
        calls: AtomicUsize::new(0),
        fail: false,
    };
    let error = RegisteredExporter {
        exporter: &exporter,
        execution: &execution,
    }
    .export_csv(&deck, &output.join("missing.csv"))
    .unwrap_err();
    assert!(matches!(
        error,
        RegisteredExportError::Storage(StoreError::Workspace(WorkspaceError::Io(_)))
    ));
    assert!(error.source().unwrap().source().is_some());
    assert_eq!(exporter.calls.load(Ordering::SeqCst), 1);
    assert_eq!(execution.workspace().unwrap().artifact_names().count(), 0);
    drop(execution);
    jobs.close().unwrap();
    assert_eq!(
        std::fs::read(original).unwrap(),
        b"preserve external export"
    );
}

#[tokio::test]
async fn refuses_an_aliased_output_directory_before_writing_external_data() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    jobs.reserve(owner, names, GenerationOptions::default())
        .unwrap()
        .submit()
        .unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let output = execution.workspace().unwrap().output_dir().to_owned();
    let external = tempfile::tempdir().unwrap();
    let original = external.path().join("01-study.csv");
    std::fs::write(&original, "preserve original").unwrap();
    std::fs::rename(&output, output.with_extension("saved")).unwrap();
    std::os::unix::fs::symlink(external.path(), &output).unwrap();
    let mut deck = Deck::new("study".into());
    deck.add_flashcard(Flashcard {
        front: "{{c1::generated}}".into(),
        back: "new content".into(),
        ..Flashcard::default()
    });
    let error = RegisteredExporter {
        exporter: &CsvDeckExporter,
        execution: &execution,
    }
    .export_csv(&deck, &output.join("01-study.csv"))
    .unwrap_err();
    assert!(matches!(
        error,
        RegisteredExportError::Storage(StoreError::Workspace(WorkspaceError::InvalidArtifact))
    ));
    assert_eq!(
        std::fs::read_to_string(&original).unwrap(),
        "preserve original"
    );
    assert_eq!(std::fs::read_dir(external.path()).unwrap().count(), 1);
    drop(execution);
    jobs.close().unwrap();
    assert_eq!(
        std::fs::read_to_string(original).unwrap(),
        "preserve original"
    );
}

#[tokio::test]
async fn rejects_traversal_and_nested_aliases_before_overwriting_files() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    jobs.reserve(owner, names, GenerationOptions::default())
        .unwrap()
        .submit()
        .unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let (input, output) = {
        let workspace = execution.workspace().unwrap();
        (
            workspace.input_dir().to_owned(),
            workspace.output_dir().to_owned(),
        )
    };
    let external = tempfile::tempdir().unwrap();
    let internal_original = input.join("preserve.csv");
    let external_original = external.path().join("preserve.csv");
    for path in [&internal_original, &external_original] {
        std::fs::write(path, b"preserve original contents").unwrap();
    }
    std::os::unix::fs::symlink(external.path(), output.join("linked")).unwrap();
    let mut deck = Deck::new("study".into());
    deck.add_flashcard(Flashcard {
        front: "{{c1::generated}}".into(),
        back: "replacement content".into(),
        ..Flashcard::default()
    });
    let registered = RegisteredExporter {
        exporter: &CsvDeckExporter,
        execution: &execution,
    };
    for (path, original) in [
        (output.join("../input/preserve.csv"), &internal_original),
        (output.join("linked/preserve.csv"), &external_original),
    ] {
        let error = registered.export_csv(&deck, &path).unwrap_err();
        assert_eq!(
            std::fs::read(original).unwrap(),
            b"preserve original contents"
        );
        assert!(matches!(
            error,
            RegisteredExportError::Storage(StoreError::Workspace(WorkspaceError::InvalidArtifact))
        ));
        assert_eq!(execution.workspace().unwrap().artifact_names().count(), 0);
    }
    assert_eq!(std::fs::read_dir(external.path()).unwrap().count(), 1);
    drop(execution);
    jobs.close().unwrap();
    assert_eq!(
        std::fs::read(external_original).unwrap(),
        b"preserve original contents"
    );
}

#[tokio::test]
async fn validates_output_components_and_publishes_new_and_existing_csvs() {
    use std::{
        ffi::OsString,
        os::unix::ffi::OsStringExt,
        sync::atomic::{AtomicUsize, Ordering},
    };
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    jobs.reserve(
        owner.clone(),
        safe_filenames(&["study.pdf".into()]).unwrap(),
        GenerationOptions::default(),
    )
    .unwrap()
    .submit()
    .unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let output = execution.workspace().unwrap().output_dir().to_owned();
    std::fs::create_dir(output.join("nested")).unwrap();
    std::fs::create_dir(output.join("directory.csv")).unwrap();
    std::fs::write(output.join("file.csv"), b"preserve regular file").unwrap();
    let boundary = BoundaryExporter {
        calls: AtomicUsize::new(0),
        fail: false,
    };
    let deck = Deck::new("study".into());
    let registered = RegisteredExporter {
        exporter: &boundary,
        execution: &execution,
    };
    for path in [
        output.clone(),
        output.join("cards.txt"),
        output.join("directory.csv"),
        output.join("file.csv/cards.csv"),
        output.join("missing/cards.csv"),
        output.join(OsString::from_vec(b"invalid-\xff.csv".to_vec())),
    ] {
        assert!(matches!(
            registered.export_csv(&deck, &path),
            Err(RegisteredExportError::Storage(_))
        ));
    }
    assert_eq!(boundary.calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        std::fs::read(output.join("file.csv")).unwrap(),
        b"preserve regular file"
    );
    assert_eq!(execution.workspace().unwrap().artifact_names().count(), 0);
    let mut deck = deck;
    deck.add_flashcard(Flashcard {
        front: "{{c1::generated}}".into(),
        back: "new content".into(),
        ..Flashcard::default()
    });
    let registered = RegisteredExporter {
        exporter: &CsvDeckExporter,
        execution: &execution,
    };
    for name in ["new.csv", "file.csv", "nested/cards.CSV"] {
        registered.export_csv(&deck, &output.join(name)).unwrap();
        let actual = std::fs::read(output.join(name)).unwrap();
        assert_eq!(actual, b"\"{{c1::generated}}\",\"new content\"\r\n");
        let mut download = jobs
            .artifact(&owner, &execution.snapshot().id, name)
            .unwrap()
            .unwrap();
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut download, &mut bytes).unwrap();
        assert_eq!(bytes, actual);
    }
    assert_eq!(execution.workspace().unwrap().artifact_names().count(), 3);
    drop(execution);
    jobs.close().unwrap();
}
