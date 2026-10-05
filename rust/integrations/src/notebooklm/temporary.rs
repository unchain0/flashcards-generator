use super::{NotebookLMClient, NotebookLMError, bounded_response, protocol};
use flashcards_services::notebooklm::{
    Notebook, NotebookLMGateway, TemporaryNotebook, TemporaryNotebookFactory,
};
use reqwest::RequestBuilder;
use serde_json::json;
use std::time::Duration;

pub struct TemporaryNotebookScope<'a> {
    client: &'a NotebookLMClient,
    owned: OwnedTemporaryNotebook,
}

struct OwnedTemporaryNotebook {
    client: NotebookLMClient,
    notebook: Notebook,
    cleanup: Option<RequestBuilder>,
}

impl TemporaryNotebookFactory for NotebookLMClient {
    type Gateway = Self;
    type Scope<'a> = TemporaryNotebookScope<'a>;

    fn is_transient_error(&self, error: &NotebookLMError) -> bool {
        match error {
            NotebookLMError::Status(status) => status.as_u16() == 429 || status.is_server_error(),
            NotebookLMError::Http(error) => {
                error.is_connect() || error.is_timeout() || error.is_body() || error.is_decode()
            }
            NotebookLMError::Timeout(_)
            | NotebookLMError::SourceFailed
            | NotebookLMError::GenerationFailed => true,
            NotebookLMError::Io(error) => matches!(
                error.kind(),
                std::io::ErrorKind::Interrupted
                    | std::io::ErrorKind::TimedOut
                    | std::io::ErrorKind::WouldBlock
            ),
            NotebookLMError::Upload {
                cause,
                cleanup_failed: false,
            } => self.is_transient_error(cause),
            _ => false,
        }
    }

    async fn wait(&self, delay: Duration) {
        tokio::time::sleep(delay).await;
    }

    async fn create_temporary_notebook(
        &self,
        title: &str,
    ) -> Result<Self::Scope<'_>, NotebookLMError> {
        let client = self.clone();
        let title = title.to_owned();
        let (sender, receiver) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(async move {
            let result = create_owned(client, &title).await;
            drop(sender.send(result));
            Ok(())
        });
        self.cleanup_tasks
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(Ok(task));
        let owned = receiver.await.map_err(|_| NotebookLMError::RpcFailure)??;
        Ok(TemporaryNotebookScope {
            client: self,
            owned,
        })
    }
}

impl TemporaryNotebook for TemporaryNotebookScope<'_> {
    type Gateway = NotebookLMClient;
    fn notebook(&self) -> &Notebook {
        &self.owned.notebook
    }
    fn gateway(&self) -> &NotebookLMClient {
        self.client
    }

    async fn close(&mut self) -> Result<(), NotebookLMError> {
        let Some(request) = &self.owned.cleanup else {
            return Ok(());
        };
        delete(request.try_clone().ok_or(NotebookLMError::RpcFailure)?).await?;
        self.owned.cleanup = None;
        Ok(())
    }
}

async fn create_owned(
    client: NotebookLMClient,
    title: &str,
) -> Result<OwnedTemporaryNotebook, NotebookLMError> {
    let notebook = client.create_notebook(title).await?;
    let cleanup = client.rpc_request("WWINqb", &json!([[notebook.id], [2]]), None, "en")?;
    Ok(OwnedTemporaryNotebook {
        client,
        notebook,
        cleanup: Some(cleanup),
    })
}

impl Drop for OwnedTemporaryNotebook {
    fn drop(&mut self) {
        let Some(request) = self.cleanup.take() else {
            return;
        };
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::warn!("Temporary NotebookLM cleanup requires an active runtime");
            self.client
                .cleanup_tasks
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .push(Err(NotebookLMError::SourceFailed));
            return;
        };
        schedule_cleanup(&self.client, &runtime, request);
    }
}

fn schedule_cleanup(
    client: &NotebookLMClient,
    runtime: &tokio::runtime::Handle,
    request: RequestBuilder,
) {
    let task = runtime.spawn(async move {
        let result = delete(request).await;
        if result.is_err() {
            tracing::warn!("Temporary NotebookLM cleanup could not be confirmed");
        }
        result
    });
    client
        .cleanup_tasks
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .push(Ok(task));
}

async fn delete(request: RequestBuilder) -> Result<(), NotebookLMError> {
    tokio::time::timeout(Duration::from_secs(10), async {
        let response = request.send().await?;
        protocol::decode_rpc(&bounded_response(response).await?, "WWINqb")?;
        Ok(())
    })
    .await
    .map_err(NotebookLMError::Timeout)?
}

#[cfg(test)]
mod tests;
