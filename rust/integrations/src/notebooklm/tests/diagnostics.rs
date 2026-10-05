use super::*;
use flashcards_services::{
    document_export::ExportIssue, generation::GenerationError,
    prepared_generation::PreparedGenerationError,
};

#[test]
fn export_diagnostics_preserve_causes_without_disclosing_private_paths() {
    let errors: [ExportIssue<std::io::Error, NotebookLMError, std::io::Error>; 4] = [
        ExportIssue::Generation(PreparedGenerationError::Preparation(std::io::Error::other(
            "synthetic-private-path",
        ))),
        ExportIssue::Cleanup(NotebookLMError::Io(std::io::Error::other(
            "synthetic-private-path",
        ))),
        ExportIssue::Export(std::io::Error::other("synthetic-private-path")),
        ExportIssue::QualityLimit,
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), index < 3);
        assert!(!error.to_string().contains("synthetic-private-path"));
    }
    for index in [0, 1] {
        assert_eq!(
            errors[index]
                .source()
                .unwrap()
                .source()
                .unwrap()
                .to_string(),
            "synthetic-private-path"
        );
    }
    assert_eq!(
        errors[2].source().unwrap().to_string(),
        "synthetic-private-path"
    );
}

#[tokio::test]
async fn notebooklm_diagnostics_redact_request_urls_and_preserve_error_chains() {
    let http = Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .get("ftp://example.invalid/private-notebook?token=synthetic-private-value")
        .send()
        .await
        .unwrap_err();
    assert!(http.url().is_some());
    let http = NotebookLMError::from(http);
    let NotebookLMError::Http(inner) = &http else {
        panic!("HTTP errors must retain their typed cause");
    };
    assert!(inner.url().is_none());
    assert!(!format!("{http:?}").contains("synthetic-private-value"));

    let worker = tokio::spawn(std::future::pending::<()>());
    worker.abort();
    let timeout = tokio::time::timeout(Duration::ZERO, std::future::pending::<()>())
        .await
        .unwrap_err();
    let errors = [
        (NotebookLMError::Authentication, false),
        (NotebookLMError::InvalidInput, false),
        (
            NotebookLMError::Io(std::io::Error::other("synthetic-private-value")),
            true,
        ),
        (
            NotebookLMError::Profile(ProfileError::InvalidCredentials),
            true,
        ),
        (
            NotebookLMError::ProfileWorker(worker.await.unwrap_err()),
            true,
        ),
        (http, true),
        (
            NotebookLMError::Status(StatusCode::SERVICE_UNAVAILABLE),
            false,
        ),
        (
            NotebookLMError::from(serde_json::from_str::<Value>("{").unwrap_err()),
            true,
        ),
        (NotebookLMError::Schema("artifact envelope"), false),
        (NotebookLMError::ResponseTooLarge, false),
        (NotebookLMError::RpcFailure, false),
        (NotebookLMError::GenerationFailed, false),
        (NotebookLMError::SourceFailed, false),
        (NotebookLMError::Timeout(timeout), true),
    ];
    for (error, has_source) in errors {
        assert!(!error.to_string().contains("synthetic-private-value"));
        assert_eq!(error.source().is_some(), has_source);
    }
    for cleanup_failed in [true, false] {
        let error = NotebookLMError::Upload {
            cause: Box::new(NotebookLMError::Io(std::io::Error::other(
                "synthetic-private-value",
            ))),
            cleanup_failed,
        };
        assert_ne!(error.to_string(), "");
        assert!(!error.to_string().contains("synthetic-private-value"));
        assert!(error.source().unwrap().source().is_some());
    }
}

#[test]
fn generation_diagnostics_keep_causal_errors_out_of_public_messages() {
    let errors = [
        (GenerationError::InvalidInput, false),
        (
            GenerationError::Creation(NotebookLMError::Authentication),
            true,
        ),
        (
            GenerationError::Provider(NotebookLMError::SourceFailed),
            true,
        ),
        (
            GenerationError::Cleanup {
                generation: Box::new(GenerationError::Provider(NotebookLMError::Io(
                    std::io::Error::other("synthetic-private-value"),
                ))),
                cleanup: NotebookLMError::RpcFailure,
            },
            true,
        ),
    ];
    assert_eq!(
        errors[3]
            .0
            .source()
            .unwrap()
            .source()
            .unwrap()
            .source()
            .unwrap()
            .to_string(),
        "synthetic-private-value"
    );
    for (error, has_source) in errors {
        assert!(!error.to_string().contains("synthetic-private-value"));
        assert_eq!(error.source().is_some(), has_source);
    }
}

#[test]
fn prepared_generation_diagnostics_distinguish_failures_without_disclosing_document_data() {
    let errors: [PreparedGenerationError<std::io::Error, NotebookLMError>; 5] = [
        PreparedGenerationError::InvalidInput,
        PreparedGenerationError::Preparation(std::io::Error::other("synthetic-private-value")),
        PreparedGenerationError::NoContent,
        PreparedGenerationError::NoCards {
            cleanup_errors: vec![NotebookLMError::RpcFailure],
        },
        PreparedGenerationError::Generation {
            completed: Vec::new(),
            failed_chunk: 1,
            error: GenerationError::Creation(NotebookLMError::Authentication),
        },
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_ne!(error.to_string(), "");
        assert!(!error.to_string().contains("synthetic-private-value"));
        assert_eq!(error.source().is_some(), matches!(index, 1 | 4));
    }
    assert_eq!(
        errors[1].source().unwrap().to_string(),
        "synthetic-private-value"
    );
}
