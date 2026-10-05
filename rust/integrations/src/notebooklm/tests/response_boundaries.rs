use super::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::test]
async fn chunked_responses_accept_the_exact_limit_and_reject_an_extra_byte() {
    let (response, server) = local_response(chunked_response(MAX_RESPONSE_BYTES)).await;
    assert_eq!(response.content_length(), None);
    let body = bounded_response(response).await.unwrap();
    server.await.unwrap();
    assert_eq!(body.len(), MAX_RESPONSE_BYTES);
    assert!(body.bytes().all(|byte| byte == b'x'));
    let (response, server) = local_response(chunked_response(MAX_RESPONSE_BYTES + 1)).await;
    assert_eq!(response.content_length(), None);
    let result = bounded_response(response).await;
    server.await.unwrap();
    assert!(matches!(result, Err(NotebookLMError::ResponseTooLarge)));
}

fn chunked_response(length: usize) -> Vec<u8> {
    let mut wire = format!(
        "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n{length:x}\r\n"
    )
    .into_bytes();
    wire.resize(wire.len() + length, b'x');
    wire.extend_from_slice(b"\r\n0\r\n\r\n");
    wire
}

#[tokio::test]
async fn oversized_declared_responses_are_rejected_before_reading_the_missing_body() {
    let wire = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        MAX_RESPONSE_BYTES + 1
    )
    .into_bytes();
    let (response, server) = local_response(wire).await;
    assert_eq!(
        response.content_length(),
        Some(u64::try_from(MAX_RESPONSE_BYTES + 1).unwrap())
    );
    let result = bounded_response(response).await;
    server.await.unwrap();
    assert!(matches!(result, Err(NotebookLMError::ResponseTooLarge)));
}

#[tokio::test]
async fn interrupted_and_invalid_utf8_responses_keep_typed_safe_errors() {
    let (response, server) = local_response(
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nabc".to_vec(),
    )
    .await;
    let error = bounded_response(response).await.unwrap_err();
    server.await.unwrap();
    assert!(!format!("{error:?}").contains("synthetic-private-route"));
    let NotebookLMError::Http(cause) = error else {
        panic!("Interrupted response bodies must retain the HTTP error");
    };
    assert!(cause.is_decode());
    assert!(cause.url().is_none());
    assert!(cause.source().is_some());

    let (response, server) = local_response(
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n\xff\xfe".to_vec(),
    )
    .await;
    let error = bounded_response(response).await.unwrap_err();
    server.await.unwrap();
    assert!(matches!(error, NotebookLMError::Schema("UTF-8 response")));
    assert!(!format!("{error:?}").contains("synthetic-private-route"));
}

async fn local_response(wire: Vec<u8>) -> (Response, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let serve = async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut request = Vec::new();
        let mut buffer = [0_u8; 1024];
        while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
            let count = socket.read(&mut buffer).await.unwrap();
            assert!(count > 0);
            request.extend_from_slice(&buffer[..count]);
            assert!(request.len() <= 4096);
        }
        let _ = socket.write_all(&wire).await;
        let _ = socket.shutdown().await;
    };
    let server = tokio::spawn(async move {
        tokio::time::timeout(Duration::from_secs(10), serve)
            .await
            .unwrap();
    });
    let response = Client::builder()
        .timeout(Duration::from_secs(10))
        .redirect(Policy::none())
        .retry(reqwest::retry::never())
        .build()
        .unwrap()
        .get(format!("http://{address}/synthetic-private-route"))
        .send()
        .await
        .unwrap();
    (response, server)
}
