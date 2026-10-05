use super::*;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tokio::net::TcpListener;

mod deadlines;
mod transport;

#[test]
fn restricts_browser_endpoints_accounts_and_credentials_without_retaining_unrelated_cookies() {
    assert_eq!(
        endpoint_url("12345\n/devtools/browser/ab-123\n").unwrap(),
        "ws://127.0.0.1:12345/devtools/browser/ab-123"
    );
    for invalid in [
        "",
        "0\n/devtools/browser/a",
        "65536\n/devtools/browser/a",
        "1\n/devtools/browser/",
        "1\n//attacker.example/a",
        "1\n/devtools/browser/a?x=1",
        "1\n/devtools/browser/a\nextra",
    ] {
        assert!(endpoint_url(invalid).is_err());
    }
    assert!(endpoint_url(&format!("1\n/devtools/browser/{}", "a".repeat(129))).is_err());
    assert_eq!(
        notebooklm_account(
            &json!({"type":"page","url":"https://notebooklm.google.com/?authuser=3"})
        ),
        Some(3)
    );
    for invalid in [
        "https://notebooklm.google.com.attacker.example/",
        "https://notebook.google.com.attacker.example/",
        "http://notebook.google.com/",
        "http://notebooklm.google.com/",
        "https://notebooklm.google.com:8443/",
        "https://user@notebooklm.google.com/",
        "https://:secret@notebooklm.google.com/",
        "https://notebooklm.google.com/?authuser=1&authuser=2",
        "https://notebooklm.google.com/?authuser=invalid",
    ] {
        assert_eq!(
            notebooklm_account(&json!({"type":"page","url":invalid})),
            None
        );
    }
    let raw = storage_state(&json!({"cookies":[
        {"name":"SID","value":"fake-google-session","domain":".google.com","path":"/","expires":-1,"httpOnly":true,"secure":true},
        {"name":"APP","value":"fake-app-session","domain":"notebook.google.com","path":"/"},
        {"name":"__Host-GAPS","value":"fake-account-session","domain":"accounts.google.com","path":"/"},
        {"name":"OTHER","value":"unrelated-private-cookie","domain":"example.com","path":"/"}
    ]}), 3).unwrap().unwrap();
    assert!(!raw.contains("unrelated-private-cookie"));
    let state: Value = serde_json::from_str(&raw).unwrap();
    assert_eq!(state["authuser"], 3);
    assert_eq!(state["cookies"].as_array().unwrap().len(), 3);
    assert_eq!(
        notebooklm_account(&json!({"type":"page","url":"https://notebook.google.com/?authuser=3"})),
        Some(3)
    );
    assert_eq!(state["cookies"][0]["httpOnly"], true);
    assert!(storage_state(&json!({"cookies":[]}), 0).unwrap().is_none());
    assert!(storage_state(&json!({"cookies":null}), 0).is_err());
    let cookie = json!({"name":"SID","value":"synthetic-cookie","domain":".google.com"});
    assert!(matches!(
        storage_state(&json!({"cookies":vec![cookie; 513]}), 0),
        Err(BrowserLoginError::Protocol)
    ));

    let temporary = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(temporary.path().join("companion")).unwrap();
    let user = UserId::try_from("0123456789abcdef0123456789abcdef".to_owned()).unwrap();
    let (_, held) = profiles.lock_browser(&user).unwrap();
    assert!(matches!(
        profiles.lock_browser(&user),
        Err(ProfileError::Busy)
    ));
    drop(held);
    assert!(profiles.lock_browser(&user).is_ok());
    #[cfg(unix)]
    {
        let home = profiles.browser_home(&user).unwrap();
        std::fs::remove_file(home.join(".flashcards-login.lock")).unwrap();
        let outside = temporary.path().join("outside");
        std::fs::write(&outside, "original").unwrap();
        std::os::unix::fs::symlink(&outside, home.join(".flashcards-login.lock")).unwrap();
        assert!(profiles.lock_browser(&user).is_err());
        assert_eq!(std::fs::read_to_string(outside).unwrap(), "original");
    }
}

#[tokio::test]
async fn rejects_remote_authenticated_or_ambiguous_debugger_urls_before_connecting() {
    for endpoint in [
        "not a URL",
        "wss://127.0.0.1:1234/devtools/browser/test",
        "ws://attacker.example:1234/devtools/browser/test",
        "ws://user@127.0.0.1:1234/devtools/browser/test",
        "ws://:private-password@127.0.0.1:1234/devtools/browser/test",
        "ws://127.0.0.1:1234/devtools/browser/test?token=private-token",
        "ws://127.0.0.1:1234/devtools/browser/test#private-fragment",
        "ws://127.0.0.1/devtools/browser/test",
    ] {
        assert!(matches!(
            Cdp::connect(endpoint).await,
            Err(BrowserLoginError::Protocol)
        ));
    }
    assert!(matches!(
        browser_executable(Some(Path::new("relative-browser"))),
        Err(BrowserLoginError::Unavailable)
    ));
}

#[cfg(unix)]
#[tokio::test]
async fn stops_unresponsive_browsers_and_accepts_an_already_reaped_child() {
    for graceful in [false, true] {
        let mut child = Command::new("/bin/sh")
            .args(["-c", "exec sleep 60"])
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        stop_browser(&mut child, graceful).await.unwrap();
        assert!(child.try_wait().unwrap().is_some());
    }
    let mut exited = Command::new("/bin/sh")
        .args(["-c", "exit 0"])
        .spawn()
        .unwrap();
    exited.wait().await.unwrap();
    stop_browser(&mut exited, false).await.unwrap();
    assert!(matches!(
        poll_endpoint(&mut exited, Path::new("unused-endpoint"), None).await,
        Err(BrowserLoginError::Closed)
    ));
}

#[cfg(unix)]
#[tokio::test]
async fn ignores_previous_debugger_endpoints_until_the_browser_publishes_a_new_one() {
    use std::os::unix::fs::PermissionsExt;
    let directory = tempfile::tempdir().unwrap();
    let endpoint = directory.path().join("DevToolsActivePort");
    let previous = "12345\n/devtools/browser/previous";
    std::fs::write(&endpoint, previous).unwrap();
    std::fs::set_permissions(&endpoint, std::fs::Permissions::from_mode(0o600)).unwrap();
    let mut child = Command::new("/bin/sh")
        .args(["-c", "exec sleep 60"])
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    assert!(
        tokio::time::timeout(
            Duration::from_millis(250),
            poll_endpoint(&mut child, &endpoint, Some(previous))
        )
        .await
        .is_err()
    );
    std::fs::write(&endpoint, "23456\n/devtools/browser/current").unwrap();
    assert_eq!(
        poll_endpoint(&mut child, &endpoint, Some(previous))
            .await
            .unwrap(),
        "ws://127.0.0.1:23456/devtools/browser/current"
    );
    stop_browser(&mut child, false).await.unwrap();
}

#[cfg(unix)]
#[tokio::test]
async fn browser_exit_during_login_preserves_the_session_and_releases_the_profile() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let user = UserId::try_from("0123456789abcdef0123456789abcdef".to_owned()).unwrap();
    let original = r#"{"cookies":[{"name":"SID","value":"synthetic-cookie","domain":".google.com","path":"/"}],"origins":[]}"#;
    profiles.save(&user, original).unwrap();
    let login = NotebookLMBrowserLogin::new(Some(PathBuf::from("/bin/true")));
    assert!(matches!(
        login.login(&profiles, &user).await,
        Err(BrowserLoginError::Closed)
    ));
    assert_eq!(profiles.load(&user).unwrap().as_deref(), Some(original));
    assert!(profiles.lock_browser(&user).is_ok());
}

#[tokio::test]
async fn captures_only_verified_notebooklm_sessions_over_bounded_websocket_messages() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "ws://{}/devtools/browser/test",
        listener.local_addr().unwrap()
    );
    let server = tokio::spawn(serve_capture_fixture(listener));
    let mut cdp = Cdp::connect(&endpoint).await.unwrap();
    let verified = Arc::new(AtomicUsize::new(0));
    let raw = tokio::time::timeout(
        Duration::from_secs(10),
        capture(&mut cdp, async |raw: &str| verify_capture(raw, &verified)),
    )
    .await
    .unwrap()
    .unwrap();
    assert!(raw.contains("fixture-session"));
    assert_eq!(verified.load(Ordering::SeqCst), 2);
    let error = cdp.call("BadReply", json!({})).await.unwrap_err();
    assert!(!format!("{error:?}").contains("private-cookie-value"));
    server.await.unwrap();
    assert!(matches!(
        Cdp::connect("ws://attacker.example:1234/devtools/browser/test").await,
        Err(BrowserLoginError::Protocol)
    ));
}

#[tokio::test]
async fn rejects_missing_or_excessive_target_inventories_without_verifying_credentials() {
    for targets in [Value::Null, json!(vec![json!({}); 129])] {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!(
            "ws://{}/devtools/browser/test",
            listener.local_addr().unwrap()
        );
        let server = tokio::spawn(serve_browser_replies(
            listener,
            vec![("Target.getTargets", json!({"targetInfos":targets}))],
        ));
        let mut cdp = Cdp::connect(&endpoint).await.unwrap();
        assert!(matches!(
            capture(&mut cdp, async |_: &str| panic!("No credentials expected")).await,
            Err(BrowserLoginError::Protocol)
        ));
        server.await.unwrap();
    }
}

#[tokio::test]
async fn waits_for_usable_cookies_and_propagates_non_authentication_verification_failures() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "ws://{}/devtools/browser/test",
        listener.local_addr().unwrap()
    );
    let targets = json!({"targetInfos":[
        {"type":"worker","url":"https://notebook.google.com/"},
        {"type":"page","url":"not a URL"},
        {"type":"page"},
        {"type":"page","url":"https://notebook.google.com/"}
    ]});
    let server = tokio::spawn(serve_browser_replies(
        listener,
        vec![
            ("Target.getTargets", targets.clone()),
            ("Storage.getCookies", json!({"cookies":[]})),
            ("Target.getTargets", targets),
            ("Storage.getCookies", fixture_cookies(&mut 1)),
        ],
    ));
    let mut cdp = Cdp::connect(&endpoint).await.unwrap();
    let verified = AtomicUsize::new(0);
    let error = capture(&mut cdp, async |_: &str| {
        verified.fetch_add(1, Ordering::SeqCst);
        Err(NotebookLMError::Schema("private-verification-details"))
    })
    .await
    .unwrap_err();
    assert!(matches!(error, BrowserLoginError::Verification(_)));
    assert!(!error.to_string().contains("private-verification-details"));
    assert!(matches!(
        error.source().unwrap().downcast_ref::<NotebookLMError>(),
        Some(NotebookLMError::Schema("private-verification-details"))
    ));
    assert_eq!(verified.load(Ordering::SeqCst), 1);
    server.await.unwrap();
}

async fn serve_browser_replies(listener: TcpListener, replies: Vec<(&'static str, Value)>) {
    let (stream, _) = listener.accept().await.unwrap();
    let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
    for (method, result) in replies {
        let Message::Text(raw) = socket.next().await.unwrap().unwrap() else {
            panic!("Expected a debugger command");
        };
        let request: Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(request["method"], method);
        socket
            .send(Message::Text(
                json!({"id":request["id"],"result":result})
                    .to_string()
                    .into(),
            ))
            .await
            .unwrap();
    }
}

#[tokio::test]
async fn rejects_unbounded_browser_events_and_closed_connections_without_private_diagnostics() {
    for flood in [false, true] {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!(
            "ws://{}/devtools/browser/test",
            listener.local_addr().unwrap()
        );
        let server = tokio::spawn(serve_failed_browser_command(listener, flood));
        let mut cdp = Cdp::connect(&endpoint).await.unwrap();
        let error = cdp.call("Target.getTargets", json!({})).await.unwrap_err();
        assert!(matches!(
            (&error, flood),
            (BrowserLoginError::Protocol, true) | (BrowserLoginError::Closed, false)
        ));
        assert!(!error.to_string().contains("private-browser-event"));
        server.await.unwrap();
    }
}

async fn serve_failed_browser_command(listener: TcpListener, flood: bool) {
    let (stream, _) = listener.accept().await.unwrap();
    let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
    let request = socket.next().await.unwrap().unwrap();
    assert!(matches!(request, Message::Text(_)));
    if !flood {
        socket.send(Message::Close(None)).await.unwrap();
        return;
    }
    for _ in 0..128 {
        socket
            .send(Message::Text(
                json!({
                    "method":"Target.targetChanged",
                    "params":{"private":"private-browser-event"}
                })
                .to_string()
                .into(),
            ))
            .await
            .unwrap();
    }
}

#[test]
fn rejects_non_object_mismatched_or_malformed_browser_responses() {
    for response in [
        json!(null),
        json!([]),
        json!(42),
        json!("private-browser-response"),
        json!({"id":2,"result":{}}),
        json!({"id":1,"result":null}),
        json!({"id":1,"result":[]}),
        json!({"id":1}),
        json!({"id":1,"result":{},"error":{"private":"private-browser-response"}}),
    ] {
        assert!(matches!(
            command_response(Message::Text(response.to_string().into()), 1),
            Err(BrowserLoginError::Protocol)
        ));
    }
    for frame in [
        Message::Binary(vec![1, 2].into()),
        Message::Text("invalid JSON".into()),
    ] {
        assert!(matches!(
            command_response(frame, 1),
            Err(BrowserLoginError::Protocol)
        ));
    }
    for frame in [Message::Ping(vec![].into()), Message::Pong(vec![].into())] {
        assert!(command_response(frame, 1).unwrap().is_none());
    }
    assert_eq!(
        command_response(
            Message::Text(json!({"id":1,"result":{}}).to_string().into()),
            1
        )
        .unwrap(),
        Some(json!({}))
    );
}

#[tokio::test]
async fn drives_and_closes_a_real_browser_without_accessing_google_or_existing_profiles() {
    let executable = browser_executable(
        std::env::var_os("FLASHCARDS_TEST_BROWSER")
            .as_deref()
            .map(Path::new),
    )
    .expect("Install Chrome/Chromium or set FLASHCARDS_TEST_BROWSER for native browser acceptance");
    let temporary = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(temporary.path().join("companion")).unwrap();
    let user = UserId::try_from("0123456789abcdef0123456789abcdef".to_owned()).unwrap();
    let (home, _held) = profiles.lock_browser(&user).unwrap();
    let mut command = browser_command(&executable, &home, "about:blank");
    command.args(["--headless=new", "--disable-background-networking"]);
    let mut child = command.spawn().unwrap();
    let outcome = async {
        let endpoint = wait_for_endpoint(&mut child, home.join("DevToolsActivePort"), None).await?;
        let mut cdp = Cdp::connect(&endpoint).await?;
        let targets = cdp.call("Target.getTargets", json!({})).await?;
        assert!(
            targets["targetInfos"]
                .as_array()
                .unwrap()
                .iter()
                .any(|target| target["url"] == "about:blank")
        );
        let page = targets["targetInfos"]
            .as_array()
            .unwrap()
            .iter()
            .find(|target| target["url"] == "about:blank")
            .unwrap();
        let mut page_url = Url::parse(&endpoint).unwrap();
        page_url.set_path(&format!(
            "/devtools/page/{}",
            page["targetId"].as_str().unwrap()
        ));
        let mut page_cdp = Cdp::connect(page_url.as_str()).await?;
        let automation = page_cdp
            .call(
                "Runtime.evaluate",
                json!({"expression":"navigator.webdriver", "returnByValue":true}),
            )
            .await?;
        let automation = automation["result"]["value"].as_bool().unwrap();
        cdp.call("Browser.close", json!({})).await?;
        Ok::<_, BrowserLoginError>(automation)
    }
    .await;
    stop_browser(&mut child, true).await.unwrap();
    assert!(
        !outcome.unwrap(),
        "Interactive login must not mark a human browser as WebDriver-controlled"
    );
    assert!(child.try_wait().unwrap().is_some());
    assert!(profiles.load(&user).unwrap().is_none());
}

async fn serve_capture_fixture(listener: TcpListener) {
    let (stream, _) = listener.accept().await.unwrap();
    let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
    let mut captures = 0;
    while let Some(Ok(Message::Text(raw))) = socket.next().await {
        let request: Value = serde_json::from_str(&raw).unwrap();
        let result = match request["method"].as_str().unwrap() {
            "Target.getTargets" => {
                json!({"targetInfos":[{"type":"page","url":"https://notebooklm.google.com/?authuser=3"}]})
            }
            "Storage.getCookies" => fixture_cookies(&mut captures),
            "BadReply" => {
                socket
                    .send(Message::Text(
                        json!({"id":request["id"],"error":{"message":"private-cookie-value"}})
                            .to_string()
                            .into(),
                    ))
                    .await
                    .unwrap();
                break;
            }
            _ => panic!("Unexpected command"),
        };
        socket
            .send(Message::Text(
                json!({"method":"Target.targetChanged","params":{}})
                    .to_string()
                    .into(),
            ))
            .await
            .unwrap();
        socket
            .send(Message::Text(
                json!({"id":request["id"],"result":result})
                    .to_string()
                    .into(),
            ))
            .await
            .unwrap();
    }
}

fn verify_capture(raw: &str, verified: &AtomicUsize) -> Result<(), NotebookLMError> {
    let state: Value = serde_json::from_str(raw).unwrap();
    assert_eq!(state["authuser"], 3);
    if verified.fetch_add(1, Ordering::SeqCst) == 0 {
        Err(NotebookLMError::Authentication)
    } else {
        Ok(())
    }
}

fn fixture_cookies(captures: &mut usize) -> Value {
    *captures += 1;
    if *captures == 1 {
        json!({"cookies":[]})
    } else {
        json!({"cookies":[{"name":"SID","value":"fixture-session","domain":".google.com","path":"/","expires":-1}]})
    }
}

#[tokio::test]
async fn browser_diagnostics_hide_private_causes_and_preserve_error_chains() {
    let worker = tokio::spawn(std::future::pending::<()>());
    worker.abort();
    let errors = [
        BrowserLoginError::Unavailable,
        BrowserLoginError::Closed,
        BrowserLoginError::Protocol,
        BrowserLoginError::Timeout,
        BrowserLoginError::from(std::io::Error::other("private-browser-value")),
        BrowserLoginError::from(ProfileError::InvalidCredentials),
        BrowserLoginError::Worker(worker.await.unwrap_err()),
        BrowserLoginError::Verification(NotebookLMError::Schema("private-browser-value")),
    ];
    for (index, error) in errors.iter().enumerate() {
        assert!(!error.to_string().contains("private-browser-value"));
        assert_eq!(error.source().is_some(), index >= 4);
    }
    assert_eq!(
        errors[4].source().unwrap().to_string(),
        "private-browser-value"
    );
}

#[tokio::test]
async fn rejects_malformed_credentials_before_browser_session_verification_contacts_google() {
    for raw in ["private-invalid-json", r#"{"cookies":[]}"#] {
        assert!(matches!(
            verify_session(raw).await,
            Err(NotebookLMError::Authentication)
        ));
    }
}

#[cfg(unix)]
#[tokio::test]
async fn failed_browser_start_releases_the_profile_lock_and_preserves_credentials() {
    use std::os::unix::fs::PermissionsExt;
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let user = UserId::try_from("0123456789abcdef0123456789abcdef".to_owned()).unwrap();
    let original = r#"{"cookies":[{"name":"SID","value":"synthetic-cookie","domain":".google.com","path":"/"}],"origins":[]}"#;
    profiles.save(&user, original).unwrap();
    let missing = NotebookLMBrowserLogin::new(Some(directory.path().join("missing-browser")));
    assert!(matches!(
        missing.login(&profiles, &user).await,
        Err(BrowserLoginError::Unavailable)
    ));

    let (_, held) = profiles.lock_browser(&user).unwrap();
    assert!(matches!(
        missing.login(&profiles, &user).await,
        Err(BrowserLoginError::Profile(ProfileError::Busy))
    ));
    drop(held);
    let invalid = directory.path().join("invalid-browser");
    std::fs::write(&invalid, "not an executable").unwrap();
    std::fs::set_permissions(&invalid, std::fs::Permissions::from_mode(0o700)).unwrap();
    let failing = NotebookLMBrowserLogin::new(Some(invalid));
    assert!(matches!(
        failing.login(&profiles, &user).await,
        Err(BrowserLoginError::Io(_))
    ));
    assert_eq!(profiles.load(&user).unwrap().as_deref(), Some(original));
    assert!(profiles.lock_browser(&user).is_ok());
}
