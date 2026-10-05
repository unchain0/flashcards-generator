use super::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio_tungstenite::tungstenite::protocol::frame::{
    Frame,
    coding::{Data, OpCode},
};

#[tokio::test]
async fn rejects_failed_handshakes_and_unavailable_debuggers_without_private_diagnostics() {
    assert!(matches!(
        Cdp::connect("ws://127.0.0.1:0/devtools/browser/test").await,
        Err(BrowserLoginError::Io(_))
    ));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "ws://{}/devtools/browser/test",
        listener.local_addr().unwrap()
    );
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = [0; 4096];
        assert!(stream.read(&mut request).await.unwrap() > 0);
        stream
            .write_all(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 16\r\n\r\nprivate-response")
            .await
            .unwrap();
    });
    let error = Cdp::connect(&endpoint).await.err().unwrap();
    assert!(matches!(error, BrowserLoginError::Protocol));
    assert!(!format!("{error:?}").contains("private-response"));
    server.await.unwrap();
}

#[tokio::test]
async fn rejects_a_debugger_disconnect_during_a_command() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "ws://{}/devtools/browser/test",
        listener.local_addr().unwrap()
    );
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Text(_)
        ));
        drop(socket);
    });
    let mut cdp = Cdp::connect(&endpoint).await.unwrap();
    let error = cdp.call("Target.getTargets", json!({})).await.unwrap_err();
    assert!(matches!(error, BrowserLoginError::Protocol));
    assert_eq!(
        error.to_string(),
        "Unable to communicate with NotebookLM login browser"
    );
    server.await.unwrap();
}

#[tokio::test]
async fn enforces_the_message_limit_for_whole_and_fragmented_browser_responses() {
    for fragmented in [false, true] {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!(
            "ws://{}/devtools/browser/test",
            listener.local_addr().unwrap()
        );
        let server = tokio::spawn(serve_boundary_messages(listener, fragmented));
        let mut cdp = Cdp::connect(&endpoint).await.unwrap();
        let result = cdp.call("Target.getTargets", json!({})).await.unwrap();
        assert_eq!(
            result["text"].as_str().unwrap().len(),
            MAX_MESSAGE_BYTES - 29
        );
        assert!(matches!(
            cdp.call("Target.getTargets", json!({})).await,
            Err(BrowserLoginError::Protocol)
        ));
        drop(cdp);
        server.await.unwrap();
    }
}

async fn serve_boundary_messages(listener: TcpListener, fragmented: bool) {
    let (stream, _) = listener.accept().await.unwrap();
    let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
    for excess in [0, 1] {
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Text(_)
        ));
        let text = "x".repeat(MAX_MESSAGE_BYTES - 29 + excess);
        let response = json!({"id":excess + 1,"result":{"text":text}}).to_string();
        assert_eq!(response.len(), MAX_MESSAGE_BYTES + excess);
        if fragmented {
            let halfway = response.len() / 2;
            socket
                .send(Message::Frame(Frame::message(
                    response.as_bytes()[..halfway].to_vec(),
                    OpCode::Data(Data::Text),
                    false,
                )))
                .await
                .unwrap();
            let _ = socket
                .send(Message::Frame(Frame::message(
                    response.as_bytes()[halfway..].to_vec(),
                    OpCode::Data(Data::Continue),
                    true,
                )))
                .await;
        } else {
            let _ = socket.send(Message::Text(response.into())).await;
        }
    }
}
