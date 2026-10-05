use super::*;
use reqwest::{Url, cookie::CookieStore};

#[tokio::test]
async fn public_session_file_constructor_rejects_invalid_inputs_and_preserves_typed_errors() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("synthetic-private-session.json");
    let error = NotebookLMClient::from_storage_file(&path)
        .await
        .err()
        .unwrap();
    let NotebookLMError::Io(cause) = error else {
        panic!("Missing session files must preserve their I/O cause");
    };
    assert_eq!(cause.kind(), std::io::ErrorKind::NotFound);
    assert!(matches!(
        NotebookLMClient::from_storage_file(directory.path()).await,
        Err(NotebookLMError::Authentication)
    ));
    for raw in [
        b"".as_slice(),
        b"{}".as_slice(),
        br#"{"cookies":"invalid"}"#.as_slice(),
        br#"{"cookies":["synthetic-private-cookie""#.as_slice(),
    ] {
        std::fs::write(&path, raw).unwrap();
        let error = NotebookLMClient::from_storage_file(&path)
            .await
            .err()
            .unwrap();
        assert!(matches!(error, NotebookLMError::Authentication));
        assert!(!format!("{error:?}").contains("synthetic-private"));
        assert_eq!(std::fs::read(&path).unwrap(), raw);
    }
    std::fs::write(&path, [0xff, 0xfe]).unwrap();
    let error = NotebookLMClient::from_storage_file(&path)
        .await
        .err()
        .unwrap();
    assert!(!error.to_string().contains("synthetic-private"));
    let NotebookLMError::Io(cause) = error else {
        panic!("Invalid UTF-8 must preserve its I/O cause");
    };
    assert_eq!(cause.kind(), std::io::ErrorKind::InvalidData);
    assert_eq!(std::fs::read(&path).unwrap(), [0xff, 0xfe]);
}

#[tokio::test]
async fn session_file_size_limit_accepts_exactly_four_mebibytes_and_rejects_one_more_byte() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("session.json");
    let limit = 4 * 1024 * 1024;
    let mut raw = "{}".to_owned();
    raw.extend(std::iter::repeat_n(' ', limit - raw.len()));
    std::fs::write(&path, &raw).unwrap();
    assert_eq!(read_storage_file(&path).await.unwrap(), raw);
    assert!(matches!(
        NotebookLMClient::from_storage_file(&path).await,
        Err(NotebookLMError::Authentication)
    ));
    assert_eq!(std::fs::read_to_string(&path).unwrap(), raw);
    let oversized = directory.path().join("oversized.json");
    let file = std::fs::File::create(&oversized).unwrap();
    file.set_len(u64::try_from(limit + 1).unwrap()).unwrap();
    drop(file);
    assert!(matches!(
        NotebookLMClient::from_storage_file(&oversized).await,
        Err(NotebookLMError::ResponseTooLarge)
    ));
    assert_eq!(
        std::fs::metadata(&oversized).unwrap().len(),
        u64::try_from(limit + 1).unwrap()
    );
}

#[cfg(unix)]
#[tokio::test]
async fn linked_session_files_are_rejected_without_opening_or_changing_the_target() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let directory = tempfile::tempdir().unwrap();
    let original = directory.path().join("original.json");
    let link = directory.path().join("session.json");
    std::fs::write(&original, b"synthetic-private-session").unwrap();
    std::fs::set_permissions(&original, std::fs::Permissions::from_mode(0o640)).unwrap();
    symlink(&original, &link).unwrap();
    assert!(matches!(
        NotebookLMClient::from_storage_file(&link).await,
        Err(NotebookLMError::Authentication)
    ));
    assert_eq!(
        std::fs::read(&original).unwrap(),
        b"synthetic-private-session"
    );
    assert_eq!(
        std::fs::metadata(&original).unwrap().permissions().mode() & 0o777,
        0o640
    );
}

#[tokio::test]
async fn native_request_cookies_preserve_scope_sensitivity_rotation_and_expiry() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new().route("/", get(bootstrap_fixture));
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let mut client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
    client.origin = ORIGIN.to_owned();
    for (host, path, expected) in [
        ("notebook.google.com", "/", &["SID=synthetic-session"][..]),
        ("notebooklm.google.com", "/", &["SID=synthetic-session"][..]),
        (
            "notebooklm.google.com",
            RPC_PATH,
            &["API=rpc-only", "SID=synthetic-session"][..],
        ),
        (
            "accounts.google.com",
            "/",
            &["ACCOUNT=must-not-send", "SID=synthetic-session"][..],
        ),
    ] {
        let header = client.cookie_header(path, host).unwrap();
        assert!(header.is_sensitive());
        let mut cookies = header.to_str().unwrap().split("; ").collect::<Vec<_>>();
        cookies.sort_unstable();
        assert_eq!(cookies, expected);
    }
    assert!(matches!(
        client.cookie_header("/", "example.test"),
        Err(NotebookLMError::Authentication)
    ));
    assert!(matches!(
        client.cookie_header("/", "[invalid"),
        Err(NotebookLMError::InvalidInput)
    ));
    assert_rpc_cookie(&client, "SID=synthetic-session");
    let home = Url::parse(ORIGIN).unwrap();
    let rotated =
        header::HeaderValue::from_static("SID=rotated; Domain=.google.com; Path=/; Secure");
    client
        .cookies
        .set_cookies(&mut [&rotated].into_iter(), &home);
    assert_rpc_cookie(&client, "SID=rotated");
    let expired =
        header::HeaderValue::from_static("SID=; Max-Age=0; Domain=.google.com; Path=/; Secure");
    client
        .cookies
        .set_cookies(&mut [&expired].into_iter(), &home);
    assert!(matches!(
        client.cookie_header(RPC_PATH, "notebook.google.com"),
        Err(NotebookLMError::Authentication)
    ));
    assert!(matches!(
        client.rpc_request("wXbhsf", &json!([]), None, "en"),
        Err(NotebookLMError::Authentication)
    ));
}

fn assert_rpc_cookie(client: &NotebookLMClient, expected: &str) {
    let request = client
        .rpc_request("wXbhsf", &json!([]), None, "en")
        .unwrap()
        .build()
        .unwrap();
    assert_eq!(request.url().origin().ascii_serialization(), ORIGIN);
    assert_eq!(request.headers()[header::COOKIE], expected);
    assert!(request.headers()[header::COOKIE].is_sensitive());
    assert_eq!(request.headers()["x-goog-authuser"], "2");
    assert!(request.headers()["x-goog-authuser"].is_sensitive());
}
