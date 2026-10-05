use super::*;

pub(in crate::notebooklm) fn between_metadata_and_open<F: Future>(
    operation: F,
    change: impl FnOnce(),
) -> F::Output {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    runtime.block_on(async move {
        let (release_first, wait_first) = std::sync::mpsc::channel();
        let (entered_first, first_ready) = tokio::sync::oneshot::channel();
        let first = tokio::task::spawn_blocking(move || {
            entered_first.send(()).unwrap();
            wait_first.recv_timeout(Duration::from_secs(5)).unwrap();
        });
        tokio::time::timeout(Duration::from_secs(5), first_ready)
            .await
            .unwrap()
            .unwrap();
        tokio::pin!(operation);
        std::future::poll_fn(|context| {
            assert!(operation.as_mut().poll(context).is_pending());
            std::task::Poll::Ready(())
        })
        .await;
        let (release_second, wait_second) = std::sync::mpsc::channel();
        let (entered_second, second_ready) = tokio::sync::oneshot::channel();
        let second = tokio::task::spawn_blocking(move || {
            entered_second.send(()).unwrap();
            wait_second.recv_timeout(Duration::from_secs(5)).unwrap();
        });
        release_first.send(()).unwrap();
        tokio::time::timeout(Duration::from_secs(5), second_ready)
            .await
            .unwrap()
            .unwrap();
        change();
        std::future::poll_fn(|context| {
            assert!(operation.as_mut().poll(context).is_pending());
            std::task::Poll::Ready(())
        })
        .await;
        release_second.send(()).unwrap();
        let result = tokio::time::timeout(Duration::from_secs(5), operation)
            .await
            .unwrap();
        first.await.unwrap();
        second.await.unwrap();
        result
    })
}

#[cfg(unix)]
#[test]
fn rejects_credentials_replaced_after_metadata_without_reading_the_new_session() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("storage_state.json");
    let preserved = directory.path().join("preserved.json");
    std::fs::write(&source, STORAGE).unwrap();
    let result = between_metadata_and_open(read_storage_file(&source), || {
        std::fs::rename(&source, &preserved).unwrap();
        std::fs::write(&source, "synthetic replacement session").unwrap();
    });
    assert!(matches!(result, Err(NotebookLMError::Authentication)));
    assert_eq!(std::fs::read_to_string(&preserved).unwrap(), STORAGE);
    assert_eq!(
        std::fs::read_to_string(&source).unwrap(),
        "synthetic replacement session"
    );
}

#[test]
fn bounds_credentials_that_grow_after_the_initial_metadata_check() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("storage_state.json");
    std::fs::write(&source, STORAGE).unwrap();
    let size = 4 * 1024 * 1024 + 1;
    let result = between_metadata_and_open(read_storage_file(&source), || {
        std::fs::OpenOptions::new()
            .write(true)
            .open(&source)
            .unwrap()
            .set_len(size)
            .unwrap();
    });
    assert!(matches!(result, Err(NotebookLMError::ResponseTooLarge)));
    assert_eq!(std::fs::metadata(&source).unwrap().len(), size);
    let bytes = std::fs::read(&source).unwrap();
    assert_eq!(&bytes[..STORAGE.len()], STORAGE.as_bytes());
}
