use flashcards_domain::identity::UserId;
use flashcards_integrations::{
    notebooklm::{NotebookLMClient, NotebookLMError},
    notebooklm_profiles::{LocalNotebookLMProfiles, ProfileError},
};
use flashcards_services::notebooklm::{Notebook, NotebookLMGateway};
use std::time::Duration;
use tokio::time::error::Elapsed;

pub(super) enum NotebookLMAccess {
    Authenticated,
    LoginRequired,
    Unavailable,
}

pub(super) async fn check(profiles: &LocalNotebookLMProfiles, user: &UserId) -> NotebookLMAccess {
    let result = tokio::time::timeout(Duration::from_secs(10), async {
        NotebookLMClient::for_user(profiles, user)
            .await?
            .list_notebooks()
            .await
    })
    .await;
    classify(&result)
}

fn classify(result: &Result<Result<Vec<Notebook>, NotebookLMError>, Elapsed>) -> NotebookLMAccess {
    match result {
        Ok(Ok(_)) => NotebookLMAccess::Authenticated,
        Ok(Err(
            NotebookLMError::Authentication
            | NotebookLMError::Profile(ProfileError::InvalidCredentials),
        )) => NotebookLMAccess::LoginRequired,
        _ => NotebookLMAccess::Unavailable,
    }
}

#[cfg(test)]
mod tests;
