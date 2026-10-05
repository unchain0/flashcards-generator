use flashcards_domain::identity::UserId;
use flashcards_integrations::{
    deck_exporter::CsvDeckExporter,
    document_preparation::LocalDocumentPreparer,
    local_generation::execute_job,
    local_job_store::{LocalJobStore, StoreError},
    monitoring,
    notebooklm::{NotebookLMClient, NotebookLMError},
    notebooklm_profiles::LocalNotebookLMProfiles,
};
use flashcards_services::local_jobs::JobStatus;
use std::future::Future;
use tokio::{
    sync::watch,
    task::{JoinError, JoinHandle},
};

pub struct CompanionWorker {
    stop: watch::Sender<bool>,
    task: JoinHandle<Result<(), StoreError>>,
}
impl CompanionWorker {
    #[must_use]
    pub fn start(jobs: LocalJobStore, profiles: LocalNotebookLMProfiles) -> Self {
        Self::start_with_client(jobs, move |owner| connect_client(profiles.clone(), owner))
    }

    fn start_with_client<F, Fut>(jobs: LocalJobStore, connect: F) -> Self
    where
        F: Fn(UserId) -> Fut + Send + 'static,
        Fut: Future<Output = Result<NotebookLMClient, NotebookLMError>> + Send + 'static,
    {
        let (stop, receiver) = watch::channel(false);
        Self {
            stop,
            task: tokio::spawn(run(jobs, connect, receiver)),
        }
    }
    /// # Errors
    /// Propagates worker task failures and local queue errors after requesting a graceful stop.
    pub async fn shutdown(mut self) -> Result<Result<(), StoreError>, JoinError> {
        let _ = self.stop.send(true);
        (&mut self.task).await
    }
}
impl Drop for CompanionWorker {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn connect_client(
    profiles: LocalNotebookLMProfiles,
    owner: UserId,
) -> Result<NotebookLMClient, NotebookLMError> {
    NotebookLMClient::for_user(&profiles, &owner).await
}

async fn run<F, Fut>(
    jobs: LocalJobStore,
    connect: F,
    mut stop: watch::Receiver<bool>,
) -> Result<(), StoreError>
where
    F: Fn(UserId) -> Fut + Send,
    Fut: Future<Output = Result<NotebookLMClient, NotebookLMError>> + Send,
{
    let preparer = LocalDocumentPreparer::default();
    loop {
        let execution = tokio::select! {
            biased;
            _ = stop.changed() => return Ok(()),
            execution = jobs.wait_next() => match execution? { Some(execution) => execution, None => return Ok(()) },
        };
        let client = tokio::select! {
            biased;
            _ = stop.changed() => return Ok(()),
            client = connect(execution.owner().clone()) => client,
        };
        let client = match client {
            Ok(client) => client,
            Err(error) => {
                monitoring::report_error(&error);
                jobs.finish(&execution, JobStatus::Failed)?;
                continue;
            }
        };
        let result = {
            let generation = execute_job(&jobs, execution, &preparer, &client, &CsvDeckExporter);
            tokio::pin!(generation);
            tokio::select! {
                biased;
                _ = stop.changed() => None,
                result = &mut generation => Some(result),
            }
        };
        client.finish_cleanup().await;
        match result {
            None => return Ok(()),
            Some(Err(error)) => monitoring::report_error(&error),
            Some(Ok(_)) => {}
        }
    }
}

#[cfg(test)]
mod tests;
