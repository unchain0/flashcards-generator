use super::*;
use tokio::{sync::oneshot, task::AbortHandle};

#[tokio::test(start_paused = true)]
async fn cleanup_deadline_reaps_the_canceled_task_before_returning() {
    let tasks: CleanupTasks = Arc::default();
    let (handle, mut resource) = queue_pending_cleanup(&tasks).await;
    let before = tokio::time::Instant::now();
    assert!(!drain_cleanup(&tasks).await);
    assert_eq!(before.elapsed(), Duration::from_secs(40));
    let finished = handle.is_finished();
    let released = matches!(
        resource.try_recv(),
        Err(oneshot::error::TryRecvError::Closed)
    );
    handle.abort();
    tokio::time::sleep(Duration::from_millis(1)).await;
    assert!(
        finished,
        "The timed-out cleanup task must have terminated before return"
    );
    assert!(released, "Task resources must be released before return");
    assert!(tasks.lock().unwrap().is_empty());
}

#[tokio::test(start_paused = true)]
async fn canceling_cleanup_keeps_the_active_and_unconsumed_tasks_tracked() {
    let tasks: CleanupTasks = Arc::default();
    let (ready, completed) = oneshot::channel();
    let finished = tokio::spawn(async move {
        ready.send(()).unwrap();
        Ok(())
    });
    let completed_task = finished.abort_handle();
    tasks.lock().unwrap().push(Ok(finished));
    completed.await.unwrap();
    assert!(completed_task.is_finished());
    let mut observed = Vec::new();
    for _ in 0..3 {
        observed.push(queue_pending_cleanup(&tasks).await);
    }
    let mut cleanup = Box::pin(drain_cleanup(&tasks));
    std::future::poll_fn(|context| {
        assert!(std::future::Future::poll(cleanup.as_mut(), context).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
    assert!(tasks.lock().unwrap().is_empty());
    drop(cleanup);
    assert_eq!(tasks.lock().unwrap().len(), 3);
    assert!(observed.iter().all(|(handle, _)| !handle.is_finished()));
    assert!(!drain_cleanup(&tasks).await);
    assert!(observed.iter().all(|(handle, _)| handle.is_finished()));
    for (_, mut resource) in observed {
        assert!(matches!(
            resource.try_recv(),
            Err(oneshot::error::TryRecvError::Closed)
        ));
    }
}

#[tokio::test]
async fn canceling_cleanup_preserves_failures_already_observed_before_pending_tasks() {
    let tasks: CleanupTasks = Arc::default();
    tasks
        .lock()
        .unwrap()
        .push(Err(NotebookLMError::SourceFailed));
    let (release, released) = oneshot::channel();
    let task = tokio::spawn(async move {
        released.await.unwrap();
        Ok(())
    });
    tasks.lock().unwrap().push(Ok(task));
    let mut cleanup = Box::pin(drain_cleanup(&tasks));
    std::future::poll_fn(|context| {
        assert!(std::future::Future::poll(cleanup.as_mut(), context).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
    drop(cleanup);
    release.send(()).unwrap();
    assert!(!drain_cleanup(&tasks).await);
    assert!(drain_cleanup(&tasks).await);
    assert_eq!(tasks.lock().unwrap().len(), 0);
}

#[tokio::test]
async fn a_failed_cleanup_guard_preserves_its_error_after_its_last_task_is_consumed() {
    let tasks: CleanupTasks = Arc::default();
    let guard = PendingCleanup {
        registry: tasks.clone(),
        tasks: VecDeque::new(),
        failed: true,
    };
    drop(guard);
    assert!(!drain_cleanup(&tasks).await);
    assert!(drain_cleanup(&tasks).await);
    assert_eq!(tasks.lock().unwrap().len(), 0);
}

async fn queue_pending_cleanup(tasks: &CleanupTasks) -> (AbortHandle, oneshot::Receiver<()>) {
    let (ready, started) = oneshot::channel();
    let (resource, released) = oneshot::channel();
    let task = tokio::spawn(async move {
        ready.send(()).unwrap();
        std::future::pending::<()>().await;
        drop(resource);
        Ok(())
    });
    let observed = task.abort_handle();
    tasks.lock().unwrap().push(Ok(task));
    started.await.unwrap();
    (observed, released)
}
