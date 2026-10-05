use super::*;

#[tokio::test]
async fn repeated_close_and_drop_delete_only_the_owned_notebook_once() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let state = fixture::state(&origin);
    state
        .titles
        .lock()
        .unwrap()
        .insert("existing-notebook".into(), "Preserve".into());
    let app = fixture::router(state.clone());
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let mut scope = client.create_temporary_notebook("Study").await.unwrap();
    assert_eq!(scope.notebook().id, "owned-0");
    scope.close().await.unwrap();
    scope.close().await.unwrap();
    drop(scope);
    assert!(client.finish_cleanup().await);
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
    assert_eq!(*state.deleted.lock().unwrap(), ["owned-0"]);
    assert_eq!(*state.calls.lock().unwrap(), ["CCqFvf", "WWINqb"]);
    assert_eq!(
        state
            .titles
            .lock()
            .unwrap()
            .get("existing-notebook")
            .map(String::as_str),
        Some("Preserve")
    );
}

#[test]
fn dropping_owned_state_without_a_runtime_records_failure_without_scheduling_work() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let (owned, client, state) = runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let state = fixture::state(&origin);
        let app = fixture::router(state.clone());
        let server = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
        let owned = create_owned(client.clone(), "Study").await.unwrap();
        assert!(owned.cleanup.is_some());
        server.abort();
        assert!(server.await.unwrap_err().is_cancelled());
        (owned, client, state)
    });
    drop(runtime);
    assert!(tokio::runtime::Handle::try_current().is_err());
    drop(owned);
    assert_eq!(client.cleanup_tasks.lock().unwrap().len(), 1);
    let observer = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    assert!(!observer.block_on(client.finish_cleanup()));
    assert!(observer.block_on(client.finish_cleanup()));
    assert!(state.deleted.lock().unwrap().is_empty());
    assert_eq!(*state.calls.lock().unwrap(), ["CCqFvf"]);
}
