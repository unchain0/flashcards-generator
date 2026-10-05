use super::*;

#[tokio::test]
async fn bounds_unresponsive_debugger_handshakes_and_commands() {
    for command in [false, true] {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!(
            "ws://{}/devtools/browser/test",
            listener.local_addr().unwrap()
        );
        let ready = Arc::new(tokio::sync::Notify::new());
        let server = tokio::spawn(idle_debugger(listener, ready.clone(), command));
        let client = tokio::spawn(read_targets(endpoint));
        tokio::time::timeout(Duration::from_secs(5), ready.notified())
            .await
            .unwrap();
        tokio::time::pause();
        tokio::time::advance(COMMAND_TIMEOUT + Duration::from_secs(1)).await;
        assert!(matches!(
            client.await.unwrap(),
            Err(BrowserLoginError::Timeout)
        ));
        tokio::time::resume();
        server.abort();
        assert!(server.await.unwrap_err().is_cancelled());
    }
}

async fn read_targets(endpoint: String) -> Result<Value, BrowserLoginError> {
    let mut cdp = Cdp::connect(&endpoint).await?;
    cdp.call("Target.getTargets", json!({})).await
}

async fn idle_debugger(listener: TcpListener, ready: Arc<tokio::sync::Notify>, command: bool) {
    let (stream, _) = listener.accept().await.unwrap();
    if command {
        let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Text(_)
        ));
        ready.notify_one();
        std::future::pending::<()>().await;
        drop(socket);
    } else {
        ready.notify_one();
        std::future::pending::<()>().await;
        drop(stream);
    }
}

#[cfg(unix)]
#[tokio::test]
async fn rejects_a_stale_endpoint_at_the_startup_deadline_without_changing_it() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("DevToolsActivePort");
    let original = "12345\n/devtools/browser/previous";
    std::fs::write(&path, original).unwrap();
    let mut child = Command::new("/bin/sh")
        .args(["-c", "exec sleep 60"])
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    tokio::time::pause();
    let mut waiting = Box::pin(wait_for_endpoint(
        &mut child,
        path.clone(),
        Some(original.into()),
    ));
    std::future::poll_fn(|context| {
        assert!(std::future::Future::poll(waiting.as_mut(), context).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
    tokio::time::advance(Duration::from_secs(31)).await;
    let outcome = waiting.as_mut().await;
    drop(waiting);
    tokio::time::resume();
    stop_browser(&mut child, false).await.unwrap();
    assert!(matches!(outcome, Err(BrowserLoginError::Timeout)));
    assert!(child.try_wait().unwrap().is_some());
    assert_eq!(std::fs::read_to_string(path).unwrap(), original);
}
