use super::*;
use flashcards_domain::identity::UserId;
use flashcards_services::{
    document_inputs::safe_filenames, generation_options::GenerationOptions, local_jobs::JobSnapshot,
};
use std::{io::Write, time::Duration};

#[tokio::test]
async fn processes_queued_jobs_without_credentials_and_shuts_down_while_idle() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("profiles")).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let worker = CompanionWorker::start(jobs.clone(), profiles.clone());
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let mut ids = Vec::new();
    for owner in ["a", "b"].map(|value| UserId::try_from(value.repeat(32)).unwrap()) {
        let reservation = jobs
            .reserve(owner.clone(), names.clone(), GenerationOptions::default())
            .unwrap();
        reservation
            .create_input(&names[0])
            .unwrap()
            .write_all(b"private source")
            .unwrap();
        ids.push((owner, reservation.submit().unwrap().id));
    }
    for (owner, id) in ids {
        let snapshot = tokio::time::timeout(
            Duration::from_secs(5),
            wait_for_failed_job(&jobs, &owner, &id),
        )
        .await
        .unwrap();
        assert_eq!(snapshot.artifacts, Vec::<String>::new());
        assert_eq!(snapshot.discovered_sources, 1);
        assert_eq!(snapshot.completed_sources, 0);
        assert_eq!(snapshot.skipped_sources, 0);
        assert_eq!(snapshot.failed_sources, 0);
        assert!(!format!("{snapshot:?}").contains("private source"));
        assert_eq!(profiles.load(&owner).unwrap(), None);
    }
    tokio::time::timeout(Duration::from_secs(2), worker.shutdown())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    jobs.close().unwrap();
}

async fn wait_for_failed_job(jobs: &LocalJobStore, owner: &UserId, id: &str) -> JobSnapshot {
    let mut snapshot = jobs.snapshot(owner, id).unwrap().unwrap();
    while snapshot.status != JobStatus::Failed {
        tokio::task::yield_now().await;
        snapshot = jobs.snapshot(owner, id).unwrap().unwrap();
    }
    snapshot
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn shutdown_cancels_pending_authentication_and_preserves_the_next_users_input() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let other = UserId::try_from("b".repeat(32)).unwrap();
    let (first, input) = submit_input(&jobs, &owner);
    let (second, pending_input) = submit_input(&jobs, &other);
    let started = std::sync::Arc::new(tokio::sync::Notify::new());
    let observed = started.clone();
    let expected = owner.clone();
    let worker = CompanionWorker::start_with_client(jobs.clone(), move |requested| {
        assert_eq!(requested, expected);
        started.notify_one();
        std::future::pending::<Result<NotebookLMClient, NotebookLMError>>()
    });
    tokio::time::timeout(Duration::from_secs(2), observed.notified())
        .await
        .unwrap();
    assert_eq!(
        jobs.snapshot(&owner, &first).unwrap().unwrap().status,
        JobStatus::Running
    );
    tokio::time::timeout(Duration::from_secs(2), worker.shutdown())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    let snapshot = jobs.snapshot(&owner, &first).unwrap().unwrap();
    assert_eq!(snapshot.status, JobStatus::Cancelled);
    assert_eq!(snapshot.artifacts, Vec::<String>::new());
    assert_eq!(snapshot.completed_sources, 0);
    assert_eq!(snapshot.skipped_sources, 0);
    assert_eq!(snapshot.failed_sources, 0);
    assert!(!input.exists());
    assert_eq!(
        jobs.snapshot(&other, &second).unwrap().unwrap().status,
        JobStatus::Queued
    );
    assert_eq!(
        std::fs::read(&pending_input).unwrap(),
        b"synthetic private input"
    );
    jobs.close().unwrap();
    assert!(!pending_input.exists());
}

#[cfg(target_os = "linux")]
fn submit_input(jobs: &LocalJobStore, owner: &UserId) -> (String, std::path::PathBuf) {
    use std::os::fd::AsRawFd;
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    let mut input = reservation.create_input(&names[0]).unwrap();
    input.write_all(b"synthetic private input").unwrap();
    let input = std::fs::read_link(format!("/proc/self/fd/{}", input.as_raw_fd())).unwrap();
    (reservation.submit().unwrap().id, input)
}

#[tokio::test]
async fn exits_when_store_closes() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("profiles")).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let worker = CompanionWorker::start(jobs.clone(), profiles);
    let task = worker.task.abort_handle();
    jobs.close().unwrap();
    tokio::time::timeout(Duration::from_secs(2), wait_for_worker_exit(&task))
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(2), worker.shutdown())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert!(!directory.path().join("profiles").exists());
}

#[tokio::test]
async fn dropping_an_idle_worker_aborts_its_task_and_preserves_unconsumed_input() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("profiles")).unwrap();
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let reservation = jobs
        .reserve(owner.clone(), names.clone(), GenerationOptions::default())
        .unwrap();
    reservation
        .create_input(&names[0])
        .unwrap()
        .write_all(b"synthetic queued input")
        .unwrap();
    let submitted = reservation.submit().unwrap();
    let worker = CompanionWorker::start(jobs.clone(), profiles);
    let task = worker.task.abort_handle();
    drop(worker);
    tokio::time::timeout(Duration::from_secs(2), wait_for_worker_exit(&task))
        .await
        .unwrap();
    assert_eq!(
        jobs.snapshot(&owner, &submitted.id)
            .unwrap()
            .unwrap()
            .status,
        JobStatus::Queued
    );
    assert!(!directory.path().join("profiles").exists());
    let execution = jobs.start_next().unwrap().unwrap();
    assert_eq!(execution.snapshot().id, submitted.id);
    let input = execution
        .workspace()
        .unwrap()
        .input_dir()
        .join(names[0].as_str());
    assert_eq!(std::fs::read(&input).unwrap(), b"synthetic queued input");
    drop(execution);
    assert!(!input.exists());
    jobs.close().unwrap();
}

async fn wait_for_worker_exit(task: &tokio::task::AbortHandle) {
    while !task.is_finished() {
        tokio::task::yield_now().await;
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn cancelling_shutdown_aborts_unresponsive_work_and_retains_partial_exports() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let other = UserId::try_from("b".repeat(32)).unwrap();
    let (first, input) = submit_input(&jobs, &owner);
    let (second, pending_input) = submit_input(&jobs, &other);
    let execution = jobs.start_next().unwrap().unwrap();
    {
        let mut workspace = execution.workspace().unwrap();
        std::fs::write(
            workspace.output_dir().join("partial.csv"),
            b"partial,answer\r\n",
        )
        .unwrap();
        workspace.register_artifact("partial.csv").unwrap();
    }
    let ready = std::sync::Arc::new(tokio::sync::Notify::new());
    let started = ready.clone();
    let task = tokio::spawn(async move {
        started.notify_one();
        std::future::pending::<()>().await;
        drop(execution);
        Ok(())
    });
    let observed = task.abort_handle();
    let (stop, _receiver) = watch::channel(false);
    let worker = CompanionWorker { stop, task };
    tokio::time::timeout(Duration::from_secs(2), ready.notified())
        .await
        .unwrap();
    let mut shutdown = Box::pin(worker.shutdown());
    let mut context = std::task::Context::from_waker(std::task::Waker::noop());
    assert!(std::future::Future::poll(shutdown.as_mut(), &mut context).is_pending());
    drop(shutdown);
    let stopped = tokio::time::timeout(Duration::from_secs(2), wait_for_worker_exit(&observed))
        .await
        .is_ok();
    observed.abort();
    tokio::time::timeout(Duration::from_secs(2), wait_for_worker_exit(&observed))
        .await
        .unwrap();
    let snapshot = jobs.snapshot(&owner, &first).unwrap().unwrap();
    assert_eq!(snapshot.status, JobStatus::Cancelled);
    assert_eq!(snapshot.artifacts, ["partial.csv"]);
    assert!(!input.exists());
    let mut partial = jobs
        .artifact(&owner, &first, "partial.csv")
        .unwrap()
        .unwrap();
    let mut bytes = Vec::new();
    std::io::Read::read_to_end(&mut partial, &mut bytes).unwrap();
    assert_eq!(bytes, b"partial,answer\r\n");
    assert_eq!(
        jobs.snapshot(&other, &second).unwrap().unwrap().status,
        JobStatus::Queued
    );
    assert_eq!(
        std::fs::read(&pending_input).unwrap(),
        b"synthetic private input"
    );
    jobs.close().unwrap();
    assert!(!pending_input.exists());
    assert!(
        stopped,
        "Cancelling shutdown must abort the owned worker task"
    );
}
