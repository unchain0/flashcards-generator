use super::*;
use std::fs;

#[tokio::test]
async fn preserves_preparation_error_causes_and_source_documents_without_disclosing_paths() {
    let directory = tempfile::tempdir().unwrap();
    let preparer = LocalDocumentPreparer::default();
    for (filename, content) in [
        ("private-source.txt", b"unsupported".as_slice()),
        ("private-source.PDF", b"invalid PDF".as_slice()),
        ("private-source.PPTX", b"invalid presentation".as_slice()),
    ] {
        let source = directory.path().join(filename);
        fs::write(&source, content).unwrap();
        let error = preparer.prepare(&source).await.map(drop).unwrap_err();
        match filename.rsplit('.').next().unwrap() {
            "txt" => assert!(matches!(error, PreparationError::InvalidInput)),
            "PDF" => assert!(matches!(error, PreparationError::Pdf(_))),
            _ => assert!(matches!(error, PreparationError::Presentation(_))),
        }
        assert_eq!(
            error.source().is_some(),
            filename.ends_with("PDF") || filename.ends_with("PPTX")
        );
        assert!(!error.to_string().contains(filename));
        assert!(
            !error
                .to_string()
                .contains(directory.path().to_str().unwrap())
        );
        assert_eq!(fs::read(&source).unwrap(), content);
    }
    assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 3);
    let cause = fs::File::open(directory.path().join("private-missing-file")).unwrap_err();
    let kind = cause.kind();
    let error = PreparationError::Io(cause);
    assert_eq!(
        error
            .source()
            .unwrap()
            .downcast_ref::<io::Error>()
            .unwrap()
            .kind(),
        kind
    );
    assert!(!error.to_string().contains("private-missing-file"));
}
