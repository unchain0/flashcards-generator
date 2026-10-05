use crate::generation_options::GenerationOptions;
use flashcards_domain::Flashcard;
use std::{error::Error, future::Future, path::Path, time::Duration};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArtifactStatus {
    Pending,
    Processing,
    Ready,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Notebook {
    pub id: String,
    pub title: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Artifact {
    pub id: String,
    pub status: ArtifactStatus,
}

pub trait NotebookLMGateway: Send + Sync {
    type Error: Error + Send + Sync + 'static;
    fn add_file_source(
        &self,
        notebook: &str,
        path: &Path,
    ) -> impl Future<Output = Result<String, Self::Error>> + Send;

    fn list_notebooks(&self) -> impl Future<Output = Result<Vec<Notebook>, Self::Error>> + Send;
    fn create_notebook(
        &self,
        title: &str,
    ) -> impl Future<Output = Result<Notebook, Self::Error>> + Send;
    fn add_text_source(
        &self,
        notebook: &str,
        title: &str,
        text: &str,
    ) -> impl Future<Output = Result<String, Self::Error>> + Send;
    fn generate_flashcards(
        &self,
        notebook: &str,
        sources: &[String],
        prompt: &str,
        options: &GenerationOptions,
    ) -> impl Future<Output = Result<Artifact, Self::Error>> + Send;
    fn wait_for_source(
        &self,
        notebook: &str,
        source: &str,
        timeout: Duration,
    ) -> impl Future<Output = Result<(), Self::Error>> + Send;
    fn wait_for_artifact(
        &self,
        notebook: &str,
        artifact: &str,
        timeout: Duration,
    ) -> impl Future<Output = Result<(), Self::Error>> + Send;
    fn download_flashcards(
        &self,
        notebook: &str,
        artifact: &str,
    ) -> impl Future<Output = Result<Vec<Flashcard>, Self::Error>> + Send;
    fn delete_notebook(
        &self,
        notebook: &str,
    ) -> impl Future<Output = Result<(), Self::Error>> + Send;
}

pub trait TemporaryNotebook: Send {
    type Gateway: NotebookLMGateway;
    fn notebook(&self) -> &Notebook;
    fn gateway(&self) -> &Self::Gateway;
    fn close(
        &mut self,
    ) -> impl Future<Output = Result<(), <Self::Gateway as NotebookLMGateway>::Error>> + Send;
}

pub trait TemporaryNotebookFactory: Send + Sync {
    type Gateway: NotebookLMGateway;
    type Scope<'a>: TemporaryNotebook<Gateway = Self::Gateway>
    where
        Self: 'a;
    fn is_transient_error(&self, _error: &<Self::Gateway as NotebookLMGateway>::Error) -> bool {
        false
    }
    fn wait(&self, _delay: Duration) -> impl Future<Output = ()> + Send {
        async {}
    }
    fn create_temporary_notebook(
        &self,
        title: &str,
    ) -> impl Future<Output = Result<Self::Scope<'_>, <Self::Gateway as NotebookLMGateway>::Error>> + Send;
}
