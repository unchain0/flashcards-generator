use super::*;
use flashcards_services::{document_inputs::safe_filenames, local_job_registry::JOB_RETENTION};
use std::{
    fs,
    io::{Read, Write},
    time::Duration,
};

#[test]
fn storage_diagnostics_preserve_causes_without_disclosing_job_paths() {
    let errors = [
        StoreError::Unavailable,
        StoreError::from(RegistryError::Missing),
        StoreError::from(WorkspaceError::Io(std::io::Error::other(
            "synthetic-private-job-path",
        ))),
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), index > 0);
        assert!(!error.to_string().contains("synthetic-private-job-path"));
    }
    assert_eq!(
        errors[2].source().unwrap().source().unwrap().to_string(),
        "synthetic-private-job-path"
    );
}

#[tokio::test]
async fn marks_jobs_failed_when_input_cleanup_cannot_remove_a_replaced_directory() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let snapshot = jobs
        .reserve(owner.clone(), names, GenerationOptions::default())
        .unwrap()
        .submit()
        .unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let input = execution.workspace().unwrap().input_dir().to_owned();
    fs::remove_dir(&input).unwrap();
    fs::write(&input, b"synthetic cleanup blocker").unwrap();
    assert!(matches!(
        jobs.finish(&execution, JobStatus::Running),
        Err(StoreError::Registry(RegistryError::InvalidState))
    ));
    assert_eq!(
        jobs.snapshot(&owner, &snapshot.id).unwrap().unwrap().status,
        JobStatus::Running
    );
    jobs.record_source(&execution, SourceOutcome::Completed)
        .unwrap();
    assert_eq!(
        jobs.finish(&execution, JobStatus::Completed)
            .unwrap()
            .status,
        JobStatus::Failed
    );
    assert_eq!(
        jobs.snapshot(&owner, &snapshot.id).unwrap().unwrap().status,
        JobStatus::Failed
    );
    assert_eq!(fs::read(input).unwrap(), b"synthetic cleanup blocker");
    drop(execution);
    jobs.close().unwrap();
}

#[tokio::test]
async fn rejected_completion_preserves_inputs_and_allows_the_job_to_finish() {
    let jobs = LocalJobStore::new().unwrap();
    let other_store = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"synthetic source for retry")
        .unwrap();
    reservation.submit().unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let input = execution
        .workspace()
        .unwrap()
        .input_dir()
        .join(names[0].as_str());
    assert!(matches!(
        other_store.finish(&execution, JobStatus::Cancelled),
        Err(StoreError::Registry(RegistryError::Missing))
    ));
    assert_eq!(fs::read(&input).unwrap(), b"synthetic source for retry");
    for status in [JobStatus::Queued, JobStatus::Running, JobStatus::Completed] {
        assert!(matches!(
            jobs.finish(&execution, status),
            Err(StoreError::Registry(RegistryError::InvalidState))
        ));
        assert_eq!(fs::read(&input).unwrap(), b"synthetic source for retry");
        let snapshot = jobs
            .snapshot(&owner, &execution.snapshot.id)
            .unwrap()
            .unwrap();
        assert_eq!(snapshot.status, JobStatus::Running);
        assert_eq!(snapshot.completed_sources, 0);
        assert_eq!(snapshot.artifacts, Vec::<String>::new());
        assert!(snapshot.error.is_none());
    }
    jobs.record_source(&execution, SourceOutcome::Completed)
        .unwrap();
    let completed = jobs.finish(&execution, JobStatus::Completed).unwrap();
    assert_eq!(completed.status, JobStatus::Completed);
    assert_eq!(completed.completed_sources, 1);
    assert!(!input.exists());
    assert!(matches!(
        jobs.finish(&execution, JobStatus::Cancelled),
        Err(StoreError::Registry(RegistryError::InvalidState))
    ));
    assert_eq!(
        jobs.snapshot(&owner, &completed.id)
            .unwrap()
            .unwrap()
            .status,
        JobStatus::Completed
    );
    jobs.close().unwrap();
    other_store.close().unwrap();
}

#[tokio::test]
async fn cleans_abandoned_uploads_and_retains_only_owner_accessible_outputs() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let other = UserId::try_from("b".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    let abandoned_id = reservation.id().to_owned();
    let abandoned_root = lock(&reservation.resource.workspace)
        .unwrap()
        .input_dir()
        .parent()
        .unwrap()
        .to_owned();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"private document")
        .unwrap();
    assert!(abandoned_root.exists());
    drop(reservation);
    assert!(!abandoned_root.exists());
    assert!(jobs.snapshot(&owner, &abandoned_id).unwrap().is_none());

    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    let id = reservation.id().to_owned();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"private document")
        .unwrap();
    reservation.submit().unwrap();
    assert!(jobs.snapshot(&other, &id).unwrap().is_none());
    assert!(jobs.artifact(&other, &id, "deck.csv").unwrap().is_none());
    let execution = jobs.start_next().unwrap().unwrap();
    assert_eq!(execution.snapshot.id, id);
    assert_eq!(execution.options(), &GenerationOptions::default());
    assert!(jobs.start_next().unwrap().is_none());
    let root = {
        let mut workspace = execution.workspace().unwrap();
        fs::write(
            workspace.output_dir().join("deck.csv"),
            b"question,answer\r\n",
        )
        .unwrap();
        workspace.register_artifact("deck.csv").unwrap();
        workspace.input_dir().parent().unwrap().to_owned()
    };
    jobs.record_source(&execution, SourceOutcome::Completed)
        .unwrap();
    let snapshot = jobs.finish(&execution, JobStatus::Completed).unwrap();
    assert_eq!(snapshot.artifacts, vec!["deck.csv"]);
    assert!(!root.join("input").exists());
    assert!(root.join("output/deck.csv").exists());
    let mut content = String::new();
    jobs.artifact(&owner, &id, "deck.csv")
        .unwrap()
        .unwrap()
        .read_to_string(&mut content)
        .unwrap();
    assert_eq!(content, "question,answer\r\n");
    assert!(
        jobs.artifact(&owner, &id, "../../deck.csv")
            .unwrap()
            .is_none()
    );
    assert!(jobs.artifact(&other, &id, "deck.csv").unwrap().is_none());
    drop(execution);
    jobs.close().unwrap();
    jobs.close().unwrap();
    assert!(!root.exists());
    assert!(jobs.snapshot(&owner, &id).unwrap().is_none());
    assert!(jobs.start_next().unwrap().is_none());
    assert!(matches!(
        jobs.reserve(owner, names, GenerationOptions::default()),
        Err(StoreError::Registry(RegistryError::Closed))
    ));
}

#[tokio::test]
async fn expires_workspaces_without_requests_and_stops_timer_when_store_is_dropped() {
    let (jobs, owner, id, root) = expiring_storage(Duration::from_millis(20));
    jobs.0.changed.notify_one();
    tokio::time::timeout(Duration::from_secs(2), wait_for_workspace_removal(&root))
        .await
        .unwrap();
    assert!(jobs.snapshot(&owner, &id).unwrap().is_none());
    let weak = Arc::downgrade(&jobs.0);
    drop(jobs);
    assert!(weak.upgrade().is_none());
}

fn expiring_storage(delay: Duration) -> (LocalJobStore, UserId, String, std::path::PathBuf) {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names, GenerationOptions::default())
        .unwrap();
    let root = lock(&reservation.resource.workspace)
        .unwrap()
        .input_dir()
        .parent()
        .unwrap()
        .to_owned();
    let id = reservation.id().to_owned();
    reservation.submit().unwrap();
    jobs.registry()
        .unwrap()
        .finish(
            &owner,
            &id,
            JobStatus::Cancelled,
            vec![],
            Instant::now().checked_sub(JOB_RETENTION).unwrap() + delay,
        )
        .unwrap();
    (jobs, owner, id, root)
}

#[tokio::test]
async fn expiry_returns_when_storage_disappears_before_or_during_its_deadline() {
    tokio::time::timeout(
        Duration::from_secs(2),
        expire(Weak::new(), Arc::new(Notify::new())),
    )
    .await
    .unwrap();
    let (jobs, _, _, root) = expiring_storage(Duration::from_secs(1));
    let expiration = expire(Arc::downgrade(&jobs.0), jobs.0.changed.clone());
    tokio::pin!(expiration);
    let mut context = std::task::Context::from_waker(std::task::Waker::noop());
    assert!(std::future::Future::poll(expiration.as_mut(), &mut context).is_pending());
    drop(jobs);
    assert!(!root.exists());
    tokio::time::timeout(Duration::from_secs(2), expiration)
        .await
        .unwrap();
}

#[tokio::test]
async fn expiry_preserves_workspaces_if_storage_becomes_unavailable_before_the_deadline() {
    let (jobs, _, _, root) = expiring_storage(Duration::from_secs(1));
    let expiration = expire(Arc::downgrade(&jobs.0), jobs.0.changed.clone());
    tokio::pin!(expiration);
    let mut context = std::task::Context::from_waker(std::task::Waker::noop());
    assert!(std::future::Future::poll(expiration.as_mut(), &mut context).is_pending());
    poison_lock(&jobs.0.registry);
    tokio::time::timeout(Duration::from_secs(2), expiration)
        .await
        .unwrap();
    assert!(root.exists());
    drop(jobs);
    assert!(!root.exists());
}

async fn wait_for_workspace_removal(root: &std::path::Path) {
    while root.exists() {
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

#[tokio::test]
async fn enforces_capacity_before_submission_and_releases_slots_after_upload_cancellation() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let mut reservations = Vec::new();
    for _ in 0..3 {
        reservations.push(
            jobs.reserve(owner.clone(), names.clone(), GenerationOptions::default())
                .unwrap(),
        );
    }
    assert!(matches!(
        jobs.reserve(owner.clone(), names.clone(), GenerationOptions::default()),
        Err(StoreError::Registry(RegistryError::Full))
    ));
    drop(reservations.pop());
    let replacement = jobs
        .reserve(owner, names, GenerationOptions::default())
        .unwrap();
    drop(replacement);
    drop(reservations);
    assert!(jobs.registry().unwrap().next_expiration().is_none());
}

#[tokio::test]
async fn cancelled_execution_cleans_sources_releases_worker_and_keeps_partial_exports() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let first = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    let id = first.id().to_owned();
    first
        .create_input(&names[0])
        .unwrap()
        .write_all(b"private source")
        .unwrap();
    first.submit().unwrap();
    let second = jobs
        .reserve(owner.clone(), names, GenerationOptions::default())
        .unwrap();
    let second_id = second.id().to_owned();
    second.submit().unwrap();
    let execution = jobs.start_next().unwrap().unwrap();
    let root = {
        let mut workspace = execution.workspace().unwrap();
        fs::write(
            workspace.output_dir().join("partial.csv"),
            b"partial,answer\r\n",
        )
        .unwrap();
        workspace.register_artifact("partial.csv").unwrap();
        workspace.input_dir().parent().unwrap().to_owned()
    };
    drop(execution);
    assert!(!root.join("input").exists());
    let snapshot = jobs.snapshot(&owner, &id).unwrap().unwrap();
    assert_eq!(snapshot.status, JobStatus::Cancelled);
    assert_eq!(snapshot.artifacts, vec!["partial.csv"]);
    assert!(jobs.artifact(&owner, &id, "partial.csv").unwrap().is_some());
    assert_eq!(jobs.start_next().unwrap().unwrap().snapshot.id, second_id);
    jobs.close().unwrap();
    assert!(!root.exists());
}

#[test]
fn rejects_missing_runtime_without_panicking() {
    assert!(matches!(LocalJobStore::new(), Err(StoreError::Unavailable)));
}

#[tokio::test]
async fn closing_storage_releases_every_subscribed_consumer() {
    let jobs = LocalJobStore::new().unwrap();
    let first = jobs.wait_next();
    let second = jobs.wait_next();
    tokio::pin!(first, second);
    let mut context = std::task::Context::from_waker(std::task::Waker::noop());
    assert!(std::future::Future::poll(first.as_mut(), &mut context).is_pending());
    assert!(std::future::Future::poll(second.as_mut(), &mut context).is_pending());
    jobs.close().unwrap();
    assert!(matches!(
        std::future::Future::poll(first.as_mut(), &mut context),
        std::task::Poll::Ready(Ok(None))
    ));
    assert!(matches!(
        std::future::Future::poll(second.as_mut(), &mut context),
        std::task::Poll::Ready(Ok(None))
    ));
}

#[tokio::test]
async fn failed_close_releases_consumers_stops_expiry_and_preserves_sources_until_drop() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(owner, names.clone(), GenerationOptions::default())
        .unwrap();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"preserve source after failed close")
        .unwrap();
    let input = reservation
        .resource
        .workspace
        .lock()
        .unwrap()
        .input_dir()
        .join(names[0].as_str());
    let root = input.parent().unwrap().parent().unwrap().to_owned();
    let expiry = jobs
        .0
        .expiry
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .abort_handle();
    {
        let first = jobs.wait_next();
        let second = jobs.wait_next();
        tokio::pin!(first, second);
        let mut context = std::task::Context::from_waker(std::task::Waker::noop());
        assert!(std::future::Future::poll(first.as_mut(), &mut context).is_pending());
        assert!(std::future::Future::poll(second.as_mut(), &mut context).is_pending());
        poison_lock(&jobs.0.registry);
        assert!(matches!(jobs.close(), Err(StoreError::Unavailable)));
        assert!(matches!(
            std::future::Future::poll(first.as_mut(), &mut context),
            std::task::Poll::Ready(Err(StoreError::Unavailable))
        ));
        assert!(matches!(
            std::future::Future::poll(second.as_mut(), &mut context),
            std::task::Poll::Ready(Err(StoreError::Unavailable))
        ));
    }
    tokio::time::timeout(Duration::from_secs(2), wait_for_expiry_exit(&expiry))
        .await
        .unwrap();
    assert_eq!(
        fs::read(&input).unwrap(),
        b"preserve source after failed close"
    );
    assert!(matches!(jobs.start_next(), Err(StoreError::Unavailable)));
    assert!(matches!(
        reservation.create_input(&names[0]),
        Err(StoreError::Workspace(_))
    ));
    drop(reservation);
    assert!(root.exists());
    drop(jobs);
    assert!(!root.exists());
}

#[tokio::test]
async fn dropping_storage_aborts_expiry_even_when_its_handle_lock_is_poisoned() {
    let jobs = LocalJobStore::new().unwrap();
    let expiry = jobs
        .0
        .expiry
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .abort_handle();
    poison_lock(&jobs.0.expiry);
    drop(jobs);
    tokio::time::timeout(Duration::from_secs(2), wait_for_expiry_exit(&expiry))
        .await
        .unwrap();
}

async fn wait_for_expiry_exit(task: &tokio::task::AbortHandle) {
    while !task.is_finished() {
        tokio::task::yield_now().await;
    }
}

fn poison_lock<T>(mutex: &Mutex<T>) {
    assert!(
        std::panic::catch_unwind(|| {
            let _guard = mutex.lock().unwrap();
            panic!("synthetic storage interruption");
        })
        .is_err()
    );
}

#[tokio::test]
async fn wakes_worker_for_submissions_completion_and_close() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let waiting = spawn_waiting_worker(&jobs);
    tokio::task::yield_now().await;
    assert!(!waiting.is_finished());
    let first = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap()
        .submit()
        .unwrap();
    let execution = tokio::time::timeout(Duration::from_secs(2), waiting)
        .await
        .unwrap()
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(execution.snapshot.id, first.id);
    let second = jobs
        .reserve(owner, names, GenerationOptions::default())
        .unwrap()
        .submit()
        .unwrap();
    let waiting = spawn_waiting_worker(&jobs);
    tokio::task::yield_now().await;
    assert!(!waiting.is_finished());
    drop(execution);
    let execution = tokio::time::timeout(Duration::from_secs(2), waiting)
        .await
        .unwrap()
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(execution.snapshot.id, second.id);
    drop(execution);
    let waiting = spawn_waiting_worker(&jobs);
    tokio::task::yield_now().await;
    assert!(!waiting.is_finished());
    jobs.close().unwrap();
    assert!(
        tokio::time::timeout(Duration::from_secs(2), waiting)
            .await
            .unwrap()
            .unwrap()
            .unwrap()
            .is_none()
    );
}

fn spawn_waiting_worker(
    jobs: &LocalJobStore,
) -> tokio::task::JoinHandle<Result<Option<JobExecution>, StoreError>> {
    let jobs = jobs.clone();
    tokio::spawn(async move { jobs.wait_next().await })
}

#[tokio::test]
async fn substituted_execution_identity_cannot_cancel_a_different_job_or_remove_inputs() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let first = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    first
        .create_input(&names[0])
        .unwrap()
        .write_all(b"preserve first source")
        .unwrap();
    let first = first.submit().unwrap();
    let second = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    second
        .create_input(&names[0])
        .unwrap()
        .write_all(b"preserve second source")
        .unwrap();
    let second = second.submit().unwrap();
    let mut execution = jobs.start_next().unwrap().unwrap();
    let input = execution
        .workspace()
        .unwrap()
        .input_dir()
        .join(names[0].as_str());
    execution.snapshot.id.clone_from(&second.id);
    assert!(matches!(
        jobs.finish(&execution, JobStatus::Cancelled),
        Err(StoreError::Registry(RegistryError::Missing))
    ));
    assert!(matches!(
        jobs.record_source(&execution, SourceOutcome::Completed),
        Err(StoreError::Registry(RegistryError::Missing))
    ));
    execution.snapshot.id.clone_from(&first.id);
    execution.owner = UserId::try_from("b".repeat(32)).unwrap();
    assert!(matches!(
        jobs.finish(&execution, JobStatus::Cancelled),
        Err(StoreError::Registry(RegistryError::Missing))
    ));
    assert!(matches!(
        jobs.record_source(&execution, SourceOutcome::Completed),
        Err(StoreError::Registry(RegistryError::Missing))
    ));
    execution.owner = owner.clone();
    assert_eq!(fs::read(&input).unwrap(), b"preserve first source");
    assert_eq!(
        jobs.snapshot(&owner, &first.id).unwrap().unwrap().status,
        JobStatus::Running
    );
    assert_eq!(
        jobs.snapshot(&owner, &second.id).unwrap().unwrap().status,
        JobStatus::Queued
    );
    jobs.record_source(&execution, SourceOutcome::Completed)
        .unwrap();
    jobs.finish(&execution, JobStatus::Completed).unwrap();
    assert!(!input.exists());
    drop(execution);
    let second_execution = jobs.start_next().unwrap().unwrap();
    let second_input = second_execution
        .workspace()
        .unwrap()
        .input_dir()
        .join(names[0].as_str());
    assert_eq!(fs::read(&second_input).unwrap(), b"preserve second source");
    drop(second_execution);
    jobs.close().unwrap();
}
