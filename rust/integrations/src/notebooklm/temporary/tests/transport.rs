use super::*;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[tokio::test]
async fn recognizes_truncated_response_bodies_as_transient_only_after_confirmed_cleanup() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route(
            "/",
            get(|| async { r#"{"SNlM0e":"csrf","FdrFJe":"session","cfb2h":"build"}"# }),
        )
        .route("/pending", get(std::future::pending::<String>));
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    let error = truncated_response(&client.http).await;
    let NotebookLMError::Http(cause) = &error else {
        panic!("Truncated HTTP bodies must retain their transport cause");
    };
    assert!(cause.is_decode());
    assert!(cause.url().is_none());
    assert!(!format!("{error:?}").contains("private-response"));
    assert!(client.is_transient_error(&error));
    let mut error = NotebookLMError::Upload {
        cause: Box::new(error),
        cleanup_failed: false,
    };
    assert!(client.is_transient_error(&error));
    let NotebookLMError::Upload { cleanup_failed, .. } = &mut error else {
        unreachable!();
    };
    *cleanup_failed = true;
    assert!(!client.is_transient_error(&error));
    for kind in [
        std::io::ErrorKind::Interrupted,
        std::io::ErrorKind::TimedOut,
        std::io::ErrorKind::WouldBlock,
    ] {
        assert!(client.is_transient_error(&NotebookLMError::Io(std::io::Error::from(kind))));
    }
    assert!(
        !client.is_transient_error(&NotebookLMError::Io(std::io::Error::from(
            std::io::ErrorKind::PermissionDenied,
        )))
    );
    let malformed = protocol::decode_rpc("private-response", "test").unwrap_err();
    assert!(!client.is_transient_error(&malformed));
    check_other_transport_errors(&client).await;
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn check_other_transport_errors(client: &NotebookLMClient) {
    use axum::body::HttpBody;
    let stream = futures_util::stream::once(async {
        Err::<Bytes, _>(std::io::Error::new(
            std::io::ErrorKind::Interrupted,
            "synthetic-private-upload-path",
        ))
    });
    let mut body = reqwest::Body::wrap_stream(stream);
    let error = std::future::poll_fn(|context| std::pin::Pin::new(&mut body).poll_frame(context))
        .await
        .unwrap()
        .unwrap_err();
    assert!(error.is_body());
    let error = NotebookLMError::from(error);
    assert!(client.is_transient_error(&error));
    assert!(!error.to_string().contains("synthetic-private-upload-path"));
    let socket = tokio::net::TcpSocket::new_v4().unwrap();
    socket.bind("127.0.0.1:0".parse().unwrap()).unwrap();
    let endpoint = format!("http://{}/private-response", socket.local_addr().unwrap());
    let error = client.http.get(endpoint).send().await.unwrap_err();
    assert!(error.is_connect());
    assert!(client.is_transient_error(&NotebookLMError::from(error)));
    let error = client
        .http
        .get(format!("{}/pending", client.origin))
        .timeout(Duration::from_millis(20))
        .send()
        .await
        .unwrap_err();
    assert!(error.is_timeout());
    assert!(client.is_transient_error(&NotebookLMError::from(error)));
    let error = client
        .http
        .get("ftp://example.invalid/private-response")
        .send()
        .await
        .unwrap_err();
    assert!(error.is_builder());
    assert!(!client.is_transient_error(&NotebookLMError::from(error)));
}

async fn truncated_response(client: &reqwest::Client) -> NotebookLMError {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/private-response", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (socket, _) = listener.accept().await.unwrap();
        let mut socket = BufReader::new(socket);
        let mut headers = Vec::new();
        while !headers.ends_with(b"\r\n\r\n") {
            assert!(socket.read_until(b'\n', &mut headers).await.unwrap() > 0);
            assert!(headers.len() <= 8192);
        }
        socket.get_mut().write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\nprivate-response").await.unwrap();
        socket.get_mut().shutdown().await.unwrap();
    });
    let operation = async {
        bounded_response(client.get(endpoint).send().await.unwrap())
            .await
            .unwrap_err()
    };
    let error = tokio::time::timeout(Duration::from_secs(5), operation)
        .await
        .unwrap();
    server.await.unwrap();
    error
}
