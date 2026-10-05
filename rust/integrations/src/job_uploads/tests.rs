use super::*;
use tokio::io::AsyncReadExt;

#[test]
fn upload_diagnostics_preserve_causes_without_disclosing_original_filenames() {
    let errors = [
        UploadError::Input(InvalidDocumentInput::FileCount),
        UploadError::Empty,
        UploadError::TooLarge,
        UploadError::from(io::Error::other("synthetic-private-upload-name")),
        UploadError::Store(StoreError::Unavailable),
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), matches!(index, 0 | 3 | 4));
        assert!(!error.to_string().contains("synthetic-private-upload-name"));
    }
    assert_eq!(
        errors[3].source().unwrap().to_string(),
        "synthetic-private-upload-name"
    );
}

#[tokio::test]
async fn rejects_changed_upload_lengths_without_submitting_partial_inputs() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let mut uploads = LocalUploads::default();
    uploads.start_file("source.pdf").unwrap();
    uploads.write_chunk(b"actual bytes").await.unwrap();
    uploads.files[0].2 += 1;
    let error = uploads
        .submit(&jobs, owner, GenerationOptions::default())
        .await
        .unwrap_err();
    let UploadError::Io(cause) = error else {
        panic!("Changed upload lengths must retain the I/O cause")
    };
    assert_eq!(cause.kind(), io::ErrorKind::UnexpectedEof);
    assert!(jobs.start_next().unwrap().is_none());
    jobs.close().unwrap();
}

#[tokio::test]
async fn preserves_private_streamed_inputs_and_releases_incomplete_uploads() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let mut uploads = LocalUploads::default();
    uploads.start_file("../../private.PDF").unwrap();
    uploads.write_chunk(b"first ").await.unwrap();
    uploads.write_chunk(b"second").await.unwrap();
    uploads.finish_file().unwrap();
    uploads.start_file(r"C:\private\slides.PPTX").unwrap();
    uploads.write_chunk(b"slides").await.unwrap();
    uploads.finish_file().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            uploads.files[0]
                .1
                .metadata()
                .await
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    let snapshot = uploads
        .submit(&jobs, owner.clone(), GenerationOptions::default())
        .await
        .unwrap();
    assert_eq!(snapshot.filenames, ["01-private.pdf", "02-slides.pptx"]);
    let execution = jobs.wait_next().await.unwrap().unwrap();
    let paths = {
        let workspace = execution.workspace().unwrap();
        workspace
            .filenames()
            .iter()
            .map(|name| workspace.input_dir().join(name.as_str()))
            .collect::<Vec<_>>()
    };
    assert_eq!(tokio::fs::read(&paths[0]).await.unwrap(), b"first second");
    assert_eq!(tokio::fs::read(&paths[1]).await.unwrap(), b"slides");
    drop(execution);
    assert!(!paths[0].exists());
    jobs.close().unwrap();
}

#[tokio::test]
async fn rejects_empty_inputs_invalid_file_counts_and_document_byte_limits() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let mut empty = LocalUploads::default();
    assert!(matches!(empty.finish_file(), Err(UploadError::Empty)));
    assert!(matches!(
        empty.write_chunk(b"x").await,
        Err(UploadError::Empty)
    ));
    empty.start_file("empty.pdf").unwrap();
    assert!(matches!(empty.finish_file(), Err(UploadError::Empty)));
    assert!(matches!(
        empty
            .submit(&jobs, owner.clone(), GenerationOptions::default())
            .await,
        Err(UploadError::Empty)
    ));
    assert!(matches!(
        LocalUploads::default()
            .submit(&jobs, owner.clone(), GenerationOptions::default())
            .await,
        Err(UploadError::Input(InvalidDocumentInput::FileCount))
    ));
    let mut uploads = LocalUploads::default();
    assert!(matches!(
        uploads.start_file("source.txt"),
        Err(UploadError::Input(InvalidDocumentInput::FileType))
    ));
    for _ in 0..10 {
        uploads.start_file("source.pdf").unwrap();
    }
    assert!(matches!(
        uploads.start_file("source.pdf"),
        Err(UploadError::Input(InvalidDocumentInput::FileCount))
    ));
    uploads.files.last_mut().unwrap().2 = MAX_DOCUMENT_BYTES;
    assert!(matches!(
        uploads.write_chunk(b"x").await,
        Err(UploadError::TooLarge)
    ));
    uploads.files.last_mut().unwrap().2 = 0;
    uploads.total_bytes = MAX_JOB_UPLOAD_BYTES;
    assert!(matches!(
        uploads.write_chunk(b"x").await,
        Err(UploadError::TooLarge)
    ));
    drop(uploads);
    jobs.close().unwrap();
}

#[tokio::test]
async fn preserves_stream_content_and_rejects_submission_to_a_closed_store() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let mut uploads = LocalUploads::default();
    uploads.start_file("source.pdf").unwrap();
    uploads.write_chunk(b"abc").await.unwrap();
    uploads.files[0].1.rewind().await.unwrap();
    let mut content = Vec::new();
    uploads.files[0].1.read_to_end(&mut content).await.unwrap();
    assert_eq!(content, b"abc");
    jobs.close().unwrap();
    assert!(matches!(
        uploads
            .submit(&jobs, owner, GenerationOptions::default())
            .await,
        Err(UploadError::Store(StoreError::Registry(
            flashcards_services::local_job_registry::RegistryError::Closed
        )))
    ));
}
