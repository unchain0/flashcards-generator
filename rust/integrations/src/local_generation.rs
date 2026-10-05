use crate::job_workspace::WorkspaceError;
use crate::local_job_store::{JobExecution, LocalJobStore, StoreError};
use flashcards_services::{
    deck_exporter::DeckExporter,
    document_export::{DocumentBatch, generate_batch_csv},
    document_preparation::DocumentPreparer,
    local_jobs::{JobSnapshot, JobStatus},
    notebooklm::TemporaryNotebookFactory,
};
use std::{error::Error, fmt, path::Path};

#[derive(Debug)]
enum RegisteredExportError<X> {
    Export(X),
    Storage(StoreError),
}
impl<X: Error> fmt::Display for RegisteredExportError<X> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Local job CSV publication failed")
    }
}
impl<X: Error + 'static> Error for RegisteredExportError<X> {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Export(error) => Some(error),
            Self::Storage(error) => Some(error),
        }
    }
}
struct RegisteredExporter<'a, X> {
    exporter: &'a X,
    execution: &'a JobExecution,
}
impl<X: DeckExporter> DeckExporter for RegisteredExporter<'_, X> {
    type Error = RegisteredExportError<X::Error>;
    fn export_csv(&self, deck: &flashcards_domain::Deck, path: &Path) -> Result<(), Self::Error> {
        let workspace = self
            .execution
            .workspace()
            .map_err(RegisteredExportError::Storage)?;
        let name = path
            .strip_prefix(workspace.output_dir())
            .ok()
            .and_then(Path::to_str)
            .ok_or(RegisteredExportError::Storage(StoreError::Workspace(
                WorkspaceError::InvalidArtifact,
            )))?
            .to_owned();
        let path = workspace
            .export_path(&name)
            .map_err(|error| RegisteredExportError::Storage(error.into()))?;
        drop(workspace);
        self.exporter
            .export_csv(deck, &path)
            .map_err(RegisteredExportError::Export)?;
        let mut workspace = self
            .execution
            .workspace()
            .map_err(RegisteredExportError::Storage)?;
        workspace
            .register_artifact(&name)
            .map_err(|error| RegisteredExportError::Storage(error.into()))
    }
}

#[cfg(all(test, unix))]
mod tests;

/// # Errors
/// Propagates workspace access and job state reporting failures; records individual source failures in the job.
pub async fn execute_job<P: DocumentPreparer, F: TemporaryNotebookFactory, X: DeckExporter>(
    jobs: &LocalJobStore,
    execution: JobExecution,
    preparer: &P,
    factory: &F,
    exporter: &X,
) -> Result<JobSnapshot, StoreError> {
    jobs.validate_execution(&execution)?;
    let (input, output, filenames) = {
        let workspace = execution.workspace()?;
        (
            workspace.input_dir().to_owned(),
            workspace.output_dir().to_owned(),
            workspace.filenames().to_vec(),
        )
    };
    let result = generate_batch_csv(
        preparer,
        factory,
        &RegisteredExporter {
            exporter,
            execution: &execution,
        },
        DocumentBatch {
            input_dir: &input,
            output_dir: &output,
            filenames: &filenames,
            options: execution.options(),
        },
        |result| {
            for issue in result.issues {
                crate::monitoring::report_error(&issue);
            }
            jobs.record_source(&execution, result.outcome)?;
            Ok::<_, StoreError>(())
        },
    )
    .await;
    let status = match result {
        Ok(status) => status,
        Err(error) => {
            crate::monitoring::report_error(&error);
            jobs.finish(&execution, JobStatus::Failed)?;
            return Err(error);
        }
    };
    jobs.finish(&execution, status)
}
