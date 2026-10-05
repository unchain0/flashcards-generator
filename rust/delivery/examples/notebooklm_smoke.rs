use flashcards_integrations::{logging, notebooklm::NotebookLMClient};
use flashcards_services::notebooklm::NotebookLMGateway;
use std::{error::Error, path::PathBuf};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    logging::initialize();
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    let [storage] = arguments.as_slice() else {
        return Err(
            "Provide one local NotebookLM storage-state file for read-only validation".into(),
        );
    };
    let result = async {
        let client = NotebookLMClient::from_storage_file(&PathBuf::from(storage)).await?;
        let notebooks = client.list_notebooks().await?;
        client.finish_cleanup().await;
        Ok::<_, flashcards_integrations::notebooklm::NotebookLMError>(notebooks.len())
    }
    .await;
    match result {
        Ok(notebook_count) => tracing::info!(
            notebook_count,
            "NotebookLM native read-only validation passed"
        ),
        Err(error) => {
            tracing::error!(error = %error, "NotebookLM native validation failed");
            return Err("NotebookLM read-only validation failed".into());
        }
    }
    Ok(())
}
