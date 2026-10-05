use crate::document_inputs::{MAX_JOB_FILES, SourceFilename};
use flashcards_domain::identity::UserId;
use serde::Serialize;
use std::{error::Error, fmt};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum JobStatus {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}
impl JobStatus {
    #[must_use]
    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct JobSnapshot {
    pub id: String,
    pub status: JobStatus,
    pub message: String,
    pub filenames: Vec<String>,
    pub discovered_sources: usize,
    pub completed_sources: usize,
    pub skipped_sources: usize,
    pub failed_sources: usize,
    pub artifacts: Vec<String>,
    pub error: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SourceOutcome {
    Completed,
    Skipped,
    Failed,
}

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidJobTransition;
impl fmt::Display for InvalidJobTransition {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Invalid local job state transition")
    }
}
impl Error for InvalidJobTransition {}

#[derive(Clone, Copy, PartialEq, Eq)]
enum TerminalStatus {
    Completed,
    Failed,
    Cancelled,
}

impl TryFrom<JobStatus> for TerminalStatus {
    type Error = InvalidJobTransition;

    fn try_from(status: JobStatus) -> Result<Self, Self::Error> {
        match status {
            JobStatus::Completed => Ok(Self::Completed),
            JobStatus::Failed => Ok(Self::Failed),
            JobStatus::Cancelled => Ok(Self::Cancelled),
            JobStatus::Queued | JobStatus::Running => Err(InvalidJobTransition),
        }
    }
}

pub struct LocalJobState {
    snapshot: JobSnapshot,
}
impl LocalJobState {
    /// # Errors
    /// Rejects invalid identifiers and empty or excessive source lists.
    pub fn new(id: &str, filenames: &[SourceFilename]) -> Result<Self, InvalidJobTransition> {
        if UserId::try_from(id.to_owned()).is_err()
            || filenames.is_empty()
            || filenames.len() > MAX_JOB_FILES
        {
            return Err(InvalidJobTransition);
        }
        Ok(Self {
            snapshot: JobSnapshot {
                id: id.to_owned(),
                status: JobStatus::Queued,
                message: "Aguardando o início da geração.".into(),
                filenames: filenames
                    .iter()
                    .map(|value| value.as_str().to_owned())
                    .collect(),
                discovered_sources: 0,
                completed_sources: 0,
                skipped_sources: 0,
                failed_sources: 0,
                artifacts: Vec::new(),
                error: None,
            },
        })
    }

    #[must_use]
    pub fn snapshot(&self) -> JobSnapshot {
        self.snapshot.clone()
    }
    #[must_use]
    pub fn status(&self) -> JobStatus {
        self.snapshot.status
    }

    /// # Errors
    /// Rejects jobs outside the queued state.
    pub fn start(&mut self) -> Result<(), InvalidJobTransition> {
        if self.status() != JobStatus::Queued {
            return Err(InvalidJobTransition);
        }
        self.snapshot.status = JobStatus::Running;
        self.snapshot.message = "Analisando os documentos...".into();
        self.snapshot.discovered_sources = self.snapshot.filenames.len();
        Ok(())
    }

    /// # Errors
    /// Rejects nonrunning jobs and results exceeding the discovered source count.
    pub fn record_source(&mut self, outcome: SourceOutcome) -> Result<(), InvalidJobTransition> {
        let snapshot = &mut self.snapshot;
        if snapshot.status != JobStatus::Running
            || snapshot.completed_sources + snapshot.skipped_sources + snapshot.failed_sources
                >= snapshot.discovered_sources
        {
            return Err(InvalidJobTransition);
        }
        match outcome {
            SourceOutcome::Completed => snapshot.completed_sources += 1,
            SourceOutcome::Skipped => snapshot.skipped_sources += 1,
            SourceOutcome::Failed => snapshot.failed_sources += 1,
        }
        snapshot.message = "Processando os documentos...".into();
        Ok(())
    }

    /// # Errors
    /// Rejects invalid terminal transitions and completion with unfinished or failed sources.
    pub fn validate_finish(&self, status: JobStatus) -> Result<(), InvalidJobTransition> {
        self.validated_finish(status).map(|_| ())
    }

    fn validated_finish(&self, status: JobStatus) -> Result<TerminalStatus, InvalidJobTransition> {
        let terminal = TerminalStatus::try_from(status)?;
        let snapshot = &self.snapshot;
        if snapshot.status.is_terminal()
            || (snapshot.status == JobStatus::Queued && terminal != TerminalStatus::Cancelled)
            || (terminal == TerminalStatus::Completed
                && (snapshot.failed_sources > 0
                    || snapshot.completed_sources + snapshot.skipped_sources
                        != snapshot.discovered_sources))
        {
            return Err(InvalidJobTransition);
        }
        Ok(terminal)
    }

    /// # Errors
    /// Rejects invalid terminal transitions and completion with unfinished or failed sources.
    pub fn finish(
        &mut self,
        status: JobStatus,
        artifacts: Vec<String>,
    ) -> Result<(), InvalidJobTransition> {
        let terminal = self.validated_finish(status)?;
        let snapshot = &mut self.snapshot;
        snapshot.status = status;
        snapshot.artifacts = artifacts;
        snapshot.artifacts.sort();
        snapshot.artifacts.dedup();
        match terminal {
            TerminalStatus::Completed => {
                snapshot.message = "Geração concluída.".into();
            }
            TerminalStatus::Cancelled => {
                snapshot.message = "Geração cancelada.".into();
            }
            TerminalStatus::Failed => {
                snapshot.message =
                    "A geração falhou. Confira a conexão do NotebookLM e tente novamente.".into();
                snapshot.error = Some("Falha na geração local.".into());
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
