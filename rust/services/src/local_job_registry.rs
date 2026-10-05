use crate::{
    document_inputs::SourceFilename,
    local_jobs::{InvalidJobTransition, JobSnapshot, JobStatus, LocalJobState, SourceOutcome},
};
use flashcards_domain::identity::UserId;
use std::{
    collections::{BTreeMap, VecDeque},
    error::Error,
    fmt,
    time::{Duration, Instant},
};

pub const MAX_ACTIVE_JOBS: usize = 3;
pub const MAX_RETAINED_JOBS: usize = 20;
pub const JOB_RETENTION: Duration = Duration::from_mins(30);

#[derive(Debug, PartialEq, Eq)]
pub enum RegistryError {
    Closed,
    Full,
    Missing,
    InvalidState,
}
impl fmt::Display for RegistryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Closed => "Local generation is closed",
            Self::Full => "Local generation queue is full",
            Self::Missing => "Local generation job not found",
            Self::InvalidState => "Invalid local generation job state",
        })
    }
}
impl Error for RegistryError {}
impl From<InvalidJobTransition> for RegistryError {
    fn from(_: InvalidJobTransition) -> Self {
        Self::InvalidState
    }
}

struct Entry<R> {
    owner: UserId,
    state: LocalJobState,
    resource: R,
    submitted: bool,
    finished_at: Option<Instant>,
}

pub struct LocalJobRegistry<R> {
    jobs: BTreeMap<String, Entry<R>>,
    queue: VecDeque<String>,
    closed: bool,
}
impl<R> Default for LocalJobRegistry<R> {
    fn default() -> Self {
        Self {
            jobs: BTreeMap::new(),
            queue: VecDeque::new(),
            closed: false,
        }
    }
}
impl<R> LocalJobRegistry<R> {
    /// # Errors
    /// Rejects a closed or full registry, duplicate identifiers, and invalid initial jobs.
    pub fn reserve(
        &mut self,
        owner: UserId,
        id: &str,
        filenames: &[SourceFilename],
        resource: R,
        now: Instant,
    ) -> Result<JobSnapshot, RegistryError> {
        if self.closed {
            return Err(RegistryError::Closed);
        }
        self.prune(now);
        if self.jobs.contains_key(id) {
            return Err(RegistryError::InvalidState);
        }
        let state = LocalJobState::new(id, filenames)?;
        if self
            .jobs
            .values()
            .filter(|entry| !entry.state.status().is_terminal())
            .count()
            >= MAX_ACTIVE_JOBS
        {
            return Err(RegistryError::Full);
        }
        if self.jobs.len() >= MAX_RETAINED_JOBS {
            let oldest = self
                .jobs
                .iter()
                .filter_map(|(id, entry)| entry.finished_at.map(|time| (id.clone(), time)))
                .min_by_key(|(_, time)| *time)
                .ok_or(RegistryError::Full)?
                .0;
            self.remove(&oldest);
        }
        let snapshot = state.snapshot();
        self.jobs.insert(
            id.to_owned(),
            Entry {
                owner,
                state,
                resource,
                submitted: false,
                finished_at: None,
            },
        );
        Ok(snapshot)
    }

    /// # Errors
    /// Rejects a closed registry, missing or foreign jobs, and already submitted or nonqueued jobs.
    pub fn submit(&mut self, owner: &UserId, id: &str) -> Result<JobSnapshot, RegistryError> {
        if self.closed {
            return Err(RegistryError::Closed);
        }
        let entry = self.entry_mut(owner, id)?;
        if entry.submitted || entry.state.status() != JobStatus::Queued {
            return Err(RegistryError::InvalidState);
        }
        entry.submitted = true;
        let snapshot = entry.state.snapshot();
        self.queue.push_back(id.to_owned());
        Ok(snapshot)
    }

    /// # Errors
    /// Reports missing queue entries or invalid transitions without removing the queued entry.
    pub fn start_next(&mut self) -> Result<Option<(UserId, JobSnapshot)>, RegistryError> {
        if self.closed
            || self
                .jobs
                .values()
                .any(|entry| entry.state.status() == JobStatus::Running)
        {
            return Ok(None);
        }
        let Some(id) = self.queue.front().cloned() else {
            return Ok(None);
        };
        let entry = self.jobs.get_mut(&id).ok_or(RegistryError::Missing)?;
        entry.state.start()?;
        let execution = (entry.owner.clone(), entry.state.snapshot());
        self.queue.pop_front();
        Ok(Some(execution))
    }

    pub fn snapshot(&mut self, owner: &UserId, id: &str, now: Instant) -> Option<JobSnapshot> {
        self.prune(now);
        self.jobs
            .get(id)
            .filter(|entry| &entry.owner == owner)
            .map(|entry| entry.state.snapshot())
    }

    pub fn resource(&mut self, owner: &UserId, id: &str, now: Instant) -> Option<&R> {
        self.prune(now);
        self.jobs
            .get(id)
            .filter(|entry| &entry.owner == owner)
            .map(|entry| &entry.resource)
    }

    /// # Errors
    /// Rejects missing or foreign jobs and invalid source-count transitions.
    pub fn record_source(
        &mut self,
        owner: &UserId,
        id: &str,
        outcome: SourceOutcome,
    ) -> Result<(), RegistryError> {
        self.entry_mut(owner, id)?.state.record_source(outcome)?;
        Ok(())
    }

    /// # Errors
    /// Rejects missing or foreign jobs and invalid completion transitions.
    pub fn validate_finish(
        &self,
        owner: &UserId,
        id: &str,
        status: JobStatus,
    ) -> Result<(), RegistryError> {
        self.jobs
            .get(id)
            .filter(|entry| &entry.owner == owner)
            .ok_or(RegistryError::Missing)?
            .state
            .validate_finish(status)?;
        Ok(())
    }

    /// # Errors
    /// Rejects missing or foreign jobs and invalid completion transitions.
    pub fn finish(
        &mut self,
        owner: &UserId,
        id: &str,
        status: JobStatus,
        artifacts: Vec<String>,
        now: Instant,
    ) -> Result<JobSnapshot, RegistryError> {
        let entry = self.entry_mut(owner, id)?;
        entry.state.finish(status, artifacts)?;
        entry.finished_at = Some(now);
        let snapshot = entry.state.snapshot();
        self.queue.retain(|queued| queued != id);
        Ok(snapshot)
    }

    /// # Errors
    /// Rejects missing or foreign jobs and submitted or nonqueued reservations.
    pub fn discard_reservation(&mut self, owner: &UserId, id: &str) -> Result<(), RegistryError> {
        let entry = self.entry_mut(owner, id)?;
        if entry.submitted || entry.state.status() != JobStatus::Queued {
            return Err(RegistryError::InvalidState);
        }
        self.remove(id);
        Ok(())
    }

    pub fn prune(&mut self, now: Instant) {
        // ponytail: scans at most twenty jobs; index expiry only if this limit increases.
        let expired = self
            .jobs
            .iter()
            .filter(|(_, entry)| {
                entry
                    .finished_at
                    .is_some_and(|time| now.saturating_duration_since(time) >= JOB_RETENTION)
            })
            .map(|(id, _)| id.clone())
            .collect::<Vec<_>>();
        for id in expired {
            self.remove(&id);
        }
    }

    pub fn close(&mut self) {
        self.closed = true;
        self.queue.clear();
        self.jobs.clear();
    }

    #[must_use]
    pub fn next_expiration(&self) -> Option<Instant> {
        self.jobs
            .values()
            .filter_map(|entry| entry.finished_at.map(|time| time + JOB_RETENTION))
            .min()
    }

    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.closed
    }

    fn entry_mut(&mut self, owner: &UserId, id: &str) -> Result<&mut Entry<R>, RegistryError> {
        self.jobs
            .get_mut(id)
            .filter(|entry| &entry.owner == owner)
            .ok_or(RegistryError::Missing)
    }
    fn remove(&mut self, id: &str) {
        self.queue.retain(|queued| queued != id);
        self.jobs.remove(id);
    }
}

#[cfg(test)]
mod tests;
