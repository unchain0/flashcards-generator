use crate::job_workspace::{LocalJobWorkspace, WorkspaceError};
use flashcards_domain::identity::UserId;
use flashcards_services::{
    document_inputs::SourceFilename,
    generation_options::GenerationOptions,
    local_job_registry::{LocalJobRegistry, RegistryError},
    local_jobs::{JobSnapshot, JobStatus, SourceOutcome},
};
use std::{
    error::Error,
    fmt,
    fs::File,
    sync::{Arc, Mutex, MutexGuard, Weak},
    time::Instant,
};
use tokio::{sync::Notify, task::JoinHandle};

#[derive(Debug)]
pub enum StoreError {
    Registry(RegistryError),
    Workspace(WorkspaceError),
    Unavailable,
}
impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Registry(error) => error.fmt(f),
            Self::Workspace(error) => error.fmt(f),
            Self::Unavailable => f.write_str("Local job storage unavailable"),
        }
    }
}
impl Error for StoreError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Registry(error) => Some(error),
            Self::Workspace(error) => Some(error),
            Self::Unavailable => None,
        }
    }
}
impl From<RegistryError> for StoreError {
    fn from(error: RegistryError) -> Self {
        Self::Registry(error)
    }
}
impl From<WorkspaceError> for StoreError {
    fn from(error: WorkspaceError) -> Self {
        Self::Workspace(error)
    }
}

#[derive(Clone)]
struct Resource {
    workspace: Arc<Mutex<LocalJobWorkspace>>,
    options: GenerationOptions,
}
struct Inner {
    registry: Mutex<LocalJobRegistry<Resource>>,
    expiry: Mutex<Option<JoinHandle<()>>>,
    changed: Arc<Notify>,
    work: Notify,
}
impl Drop for Inner {
    fn drop(&mut self) {
        if let Some(task) = self
            .expiry
            .get_mut()
            .unwrap_or_else(|error| error.into_inner())
            .take()
        {
            task.abort();
        }
    }
}

#[derive(Clone)]
pub struct LocalJobStore(Arc<Inner>);
pub struct JobReservation {
    jobs: LocalJobStore,
    owner: UserId,
    id: String,
    resource: Resource,
    submitted: bool,
}
/// Execution identity cannot be replaced by a caller.
///
/// ```compile_fail,E0616
/// use flashcards_integrations::local_job_store::JobExecution;
/// use flashcards_domain::identity::UserId;
/// fn replace_identity(execution: &mut JobExecution) {
///     execution.snapshot.id = "b".repeat(32);
///     execution.owner = UserId::try_from("b".repeat(32)).unwrap();
/// }
/// ```
pub struct JobExecution {
    owner: UserId,
    snapshot: JobSnapshot,
    jobs: LocalJobStore,
    resource: Resource,
}

impl LocalJobStore {
    /// # Errors
    /// Rejects unavailable runtimes and poisoned synchronization state.
    pub fn new() -> Result<Self, StoreError> {
        let runtime = tokio::runtime::Handle::try_current().map_err(|_| StoreError::Unavailable)?;
        let inner = Arc::new(Inner {
            registry: Mutex::new(LocalJobRegistry::default()),
            expiry: Mutex::new(None),
            changed: Arc::new(Notify::new()),
            work: Notify::new(),
        });
        let task = runtime.spawn(expire(Arc::downgrade(&inner), inner.changed.clone()));
        *lock(&inner.expiry)? = Some(task);
        Ok(Self(inner))
    }

    /// # Errors
    /// Propagates registry validation, private workspace creation, and synchronization failures.
    pub fn reserve(
        &self,
        owner: UserId,
        filenames: Vec<SourceFilename>,
        options: GenerationOptions,
    ) -> Result<JobReservation, StoreError> {
        let workspace = LocalJobWorkspace::new(owner.clone(), filenames)?;
        let filenames = workspace.filenames().to_vec();
        let id = workspace.id().to_owned();
        let resource = Resource {
            workspace: Arc::new(Mutex::new(workspace)),
            options,
        };
        self.registry()?.reserve(
            owner.clone(),
            &id,
            &filenames,
            resource.clone(),
            Instant::now(),
        )?;
        Ok(JobReservation {
            jobs: self.clone(),
            owner,
            id,
            resource,
            submitted: false,
        })
    }

    /// # Errors
    /// Propagates synchronization failures.
    pub fn snapshot(&self, owner: &UserId, id: &str) -> Result<Option<JobSnapshot>, StoreError> {
        Ok(self.registry()?.snapshot(owner, id, Instant::now()))
    }

    /// # Errors
    /// Propagates synchronization and confined artifact access failures.
    pub fn artifact(
        &self,
        owner: &UserId,
        id: &str,
        name: &str,
    ) -> Result<Option<File>, StoreError> {
        let Some(resource) = self
            .registry()?
            .resource(owner, id, Instant::now())
            .cloned()
        else {
            return Ok(None);
        };
        let file = lock(&resource.workspace)?.open_artifact(owner, name)?;
        Ok(file)
    }

    /// # Errors
    /// Propagates queue state, synchronization, and missing resource failures.
    pub fn start_next(&self) -> Result<Option<JobExecution>, StoreError> {
        let mut registry = self.registry()?;
        let Some((owner, snapshot)) = registry.start_next()? else {
            return Ok(None);
        };
        let resource = registry
            .resource(&owner, &snapshot.id, Instant::now())
            .ok_or(StoreError::Unavailable)?
            .clone();
        Ok(Some(JobExecution {
            owner,
            snapshot,
            jobs: self.clone(),
            resource,
        }))
    }

    /// # Errors
    /// Rejects invalid job transitions and propagates synchronization failures.
    pub fn record_source(
        &self,
        execution: &JobExecution,
        outcome: SourceOutcome,
    ) -> Result<(), StoreError> {
        self.validate_execution(execution)?;
        Ok(self
            .registry()?
            .record_source(&execution.owner, &execution.snapshot.id, outcome)?)
    }

    /// # Errors
    /// Propagates queue state and synchronization failures.
    pub async fn wait_next(&self) -> Result<Option<JobExecution>, StoreError> {
        await_work(self).await
    }

    /// # Errors
    /// Rejects invalid transitions and propagates synchronization failures.
    pub fn finish(
        &self,
        execution: &JobExecution,
        mut status: JobStatus,
    ) -> Result<JobSnapshot, StoreError> {
        self.validate_execution(execution)?;
        let workspace = execution.workspace()?;
        // Keep the validated transition unchanged until cleanup and completion finish.
        let mut registry = self.registry()?;
        registry.validate_finish(&execution.owner, &execution.snapshot.id, status)?;
        if let Err(error) = workspace.remove_inputs() {
            crate::monitoring::report_error(&error);
            status = JobStatus::Failed;
        }
        let artifacts = workspace.artifact_names().map(str::to_owned).collect();
        drop(workspace);
        let snapshot = registry.finish(
            &execution.owner,
            &execution.snapshot.id,
            status,
            artifacts,
            Instant::now(),
        )?;
        drop(registry);
        self.0.changed.notify_one();
        self.0.work.notify_one();
        Ok(snapshot)
    }

    /// # Errors
    /// Propagates synchronization failures while closing the registry.
    pub fn close(&self) -> Result<(), StoreError> {
        let result = self.registry().map(|mut registry| registry.close());
        self.0.work.notify_waiters();
        if let Some(task) = lock(&self.0.expiry)?.take() {
            task.abort();
        }
        result
    }

    pub(crate) fn validate_execution(&self, execution: &JobExecution) -> Result<(), StoreError> {
        if !Arc::ptr_eq(&self.0, &execution.jobs.0) {
            return Err(RegistryError::Missing.into());
        }
        let mut registry = self.registry()?;
        let resource = registry
            .resource(&execution.owner, &execution.snapshot.id, Instant::now())
            .ok_or(RegistryError::Missing)?;
        if !Arc::ptr_eq(&resource.workspace, &execution.resource.workspace) {
            return Err(RegistryError::Missing.into());
        }
        Ok(())
    }

    fn registry(&self) -> Result<MutexGuard<'_, LocalJobRegistry<Resource>>, StoreError> {
        lock(&self.0.registry)
    }
}

async fn await_work(jobs: &LocalJobStore) -> Result<Option<JobExecution>, StoreError> {
    loop {
        let notification = jobs.0.work.notified();
        if let Some(execution) = jobs.start_next()? {
            return Ok(Some(execution));
        }
        if jobs.registry()?.is_closed() {
            return Ok(None);
        }
        notification.await;
    }
}

impl JobReservation {
    #[must_use]
    pub fn id(&self) -> &str {
        &self.id
    }
    /// # Errors
    /// Propagates synchronization and confined input creation failures.
    pub fn create_input(&self, filename: &SourceFilename) -> Result<File, StoreError> {
        Ok(lock(&self.resource.workspace)?.create_input(filename)?)
    }
    /// # Errors
    /// Rejects invalid reservations and propagates synchronization failures.
    pub fn submit(mut self) -> Result<JobSnapshot, StoreError> {
        let snapshot = self.jobs.registry()?.submit(&self.owner, &self.id)?;
        self.submitted = true;
        self.jobs.0.work.notify_one();
        Ok(snapshot)
    }
}
impl Drop for JobReservation {
    fn drop(&mut self) {
        if !self.submitted
            && let Ok(mut registry) = self.jobs.registry()
        {
            let _ = registry.discard_reservation(&self.owner, &self.id);
        }
    }
}
impl JobExecution {
    #[must_use]
    pub fn owner(&self) -> &UserId {
        &self.owner
    }
    #[must_use]
    pub fn snapshot(&self) -> &JobSnapshot {
        &self.snapshot
    }
    /// # Errors
    /// Propagates poisoned workspace synchronization state.
    pub fn workspace(&self) -> Result<MutexGuard<'_, LocalJobWorkspace>, StoreError> {
        lock(&self.resource.workspace)
    }
    #[must_use]
    pub fn options(&self) -> &GenerationOptions {
        &self.resource.options
    }
}

impl Drop for JobExecution {
    fn drop(&mut self) {
        let running = self
            .jobs
            .snapshot(&self.owner, &self.snapshot.id)
            .ok()
            .flatten()
            .is_some_and(|snapshot| snapshot.status == JobStatus::Running);
        if running && let Err(error) = self.jobs.finish(self, JobStatus::Cancelled) {
            crate::monitoring::report_error(&error);
        }
    }
}

fn lock<T>(mutex: &Mutex<T>) -> Result<MutexGuard<'_, T>, StoreError> {
    mutex.lock().map_err(|_| StoreError::Unavailable)
}

async fn expire(weak: Weak<Inner>, changed: Arc<Notify>) {
    loop {
        let notification = changed.notified();
        let Some(inner) = weak.upgrade() else {
            return;
        };
        let deadline = match lock(&inner.registry) {
            Ok(registry) => registry.next_expiration(),
            Err(_) => return,
        };
        drop(inner);
        match deadline {
            Some(deadline) => tokio::select! {
                () = notification => {},
                () = tokio::time::sleep_until(deadline.into()) => {
                    let Some(inner) = weak.upgrade() else { return; };
                    if let Ok(mut registry) = lock(&inner.registry) { registry.prune(Instant::now()); }
                }
            },
            None => notification.await,
        }
    }
}

#[cfg(test)]
mod tests;
