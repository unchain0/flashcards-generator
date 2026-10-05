use super::*;

#[test]
fn empty_accounts_are_authenticated_and_only_credential_failures_require_login() {
    assert!(matches!(
        classify(&Ok(Ok(Vec::new()))),
        NotebookLMAccess::Authenticated
    ));
    for error in [
        NotebookLMError::Authentication,
        NotebookLMError::Profile(ProfileError::InvalidCredentials),
    ] {
        assert!(matches!(
            classify(&Ok(Err(error))),
            NotebookLMAccess::LoginRequired
        ));
    }
}

#[test]
fn local_and_provider_failures_do_not_discard_a_potentially_valid_session() {
    for error in [
        NotebookLMError::Profile(ProfileError::InvalidConfig),
        NotebookLMError::Profile(ProfileError::InvalidPath),
        NotebookLMError::Profile(ProfileError::Busy),
        NotebookLMError::Profile(ProfileError::TooLarge),
        NotebookLMError::Io(std::io::Error::other("private-profile-path")),
        NotebookLMError::Status(axum::http::StatusCode::SERVICE_UNAVAILABLE),
        NotebookLMError::Schema("private-provider-response"),
        NotebookLMError::RpcFailure,
        NotebookLMError::ResponseTooLarge,
    ] {
        assert!(matches!(
            classify(&Ok(Err(error))),
            NotebookLMAccess::Unavailable
        ));
    }
}

#[tokio::test]
async fn an_expired_check_deadline_does_not_require_new_credentials() {
    let elapsed = tokio::time::timeout(Duration::ZERO, std::future::pending::<()>())
        .await
        .unwrap_err();
    assert!(matches!(
        classify(&Err(elapsed)),
        NotebookLMAccess::Unavailable
    ));
}
