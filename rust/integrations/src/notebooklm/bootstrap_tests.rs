use super::*;
use axum::{Router, body::Body, http::HeaderMap, response::IntoResponse, routing::get};
use reqwest::{Url, cookie::CookieStore};
use std::sync::atomic::AtomicUsize;

#[test]
fn scopes_cookies_by_google_host_path_expiry_and_host_only_rules() {
    let raw = r#"{"cookies":[
        {"name":"SID","value":"synthetic","domain":".google.com"},
        {"name":"__Host-GAPS","value":"account-only","domain":"accounts.google.com"},
        {"name":"RPC","value":"rpc-only","domain":"notebook.google.com","path":"/_/"},
        {"name":"OLD","value":"expired","domain":".google.com","expires":1},
        {"name":"UNRELATED","value":"private","domain":"example.com"}
    ]}"#;
    let jar = protocol::Credentials::parse(raw)
        .unwrap()
        .cookie_jar()
        .unwrap();
    let home = Url::parse(ORIGIN).unwrap();
    assert_eq!(jar.cookies(&home).unwrap(), "SID=synthetic");
    let account = Url::parse("https://accounts.google.com/ServiceLogin").unwrap();
    let header = jar.cookies(&account).unwrap();
    let header = header.to_str().unwrap();
    assert!(header.contains("SID=synthetic"));
    assert!(header.contains("__Host-GAPS=account-only"));
    assert!(!header.contains("RPC="));
    assert!(!header.contains("OLD="));
    assert!(
        jar.cookies(&Url::parse("https://example.com/").unwrap())
            .is_none()
    );
    assert!(
        jar.cookies(&Url::parse("http://notebooklm.google.com/").unwrap())
            .is_none()
    );
    let rpc = Url::parse(&format!("{ORIGIN}{RPC_PATH}")).unwrap();
    assert!(
        jar.cookies(&rpc)
            .unwrap()
            .to_str()
            .unwrap()
            .contains("RPC=rpc-only")
    );
    let rotated = header::HeaderValue::from_static(
        "SID=rotated; Domain=.google.com; Path=/; Secure; HttpOnly",
    );
    jar.set_cookies(&mut [&rotated].into_iter(), &account);
    assert_eq!(jar.cookies(&home).unwrap(), "SID=rotated");
}

#[test]
fn allows_only_bounded_google_https_routes_for_bootstrap() {
    for url in [
        "https://notebook.google.com/",
        "https://notebooklm.google.com/",
        "https://accounts.google.com/ServiceLogin?continue=synthetic",
    ] {
        assert!(trusted_bootstrap_url(&Url::parse(url).unwrap(), ORIGIN));
    }
    for url in [
        "http://accounts.google.com/",
        "https://accounts.google.com:444/",
        "https://notebooklm.google.com.evil.example/",
        "https://user@accounts.google.com/",
        "https://:synthetic-password@accounts.google.com/",
        "https://accounts.google.com/#token",
        "https://support.google.com/",
        "https://example.com/",
    ] {
        assert!(!trusted_bootstrap_url(&Url::parse(url).unwrap(), ORIGIN));
    }
}

#[test]
fn preserves_valid_cookie_lifetimes_and_rejects_unrepresentable_expiry() {
    let credentials = protocol::Credentials::parse(
        r#"{"cookies":[
        {"name":"SID","value":"future","domain":".google.com","expires":4000000000},
        {"name":"SESSION","value":"session","domain":".google.com","expires":-1}
    ]}"#,
    )
    .unwrap();
    let jar = credentials.cookie_jar().unwrap();
    let header = jar.cookies(&Url::parse(ORIGIN).unwrap()).unwrap();
    let header = header.to_str().unwrap();
    assert!(header.contains("SID=future"));
    assert!(header.contains("SESSION=session"));
    let credentials = protocol::Credentials::parse(
        r#"{"cookies":[
        {"name":"SID","value":"synthetic","domain":".google.com","expires":1e300}
    ]}"#,
    )
    .unwrap();
    assert!(matches!(
        credentials.cookie_jar(),
        Err(NotebookLMError::Authentication)
    ));
}

#[tokio::test]
async fn follows_local_bootstrap_hops_and_refuses_external_targets_loops_and_missing_locations() {
    let mode = Arc::new(AtomicUsize::new(0));
    let calls = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route("/", get(bootstrap_response))
        .route(
            "/ready",
            get(|| async {
                r#"{"SNlM0e":"csrf","FdrFJe":"session","cfb2h":"build"}"#.into_response()
            }),
        )
        .with_state((mode.clone(), calls.clone()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    assert!(
        NotebookLMClient::connect(tests::STORAGE, &origin)
            .await
            .is_ok()
    );
    for selected in 1..=5 {
        mode.store(selected, Ordering::SeqCst);
        calls.store(0, Ordering::SeqCst);
        assert!(matches!(
            NotebookLMClient::connect(tests::STORAGE, &origin).await,
            Err(NotebookLMError::Authentication)
        ));
        assert_eq!(
            calls.load(Ordering::SeqCst),
            if selected == 2 { 6 } else { 1 }
        );
    }
    server.abort();
}

async fn bootstrap_response(
    axum::extract::State((mode, calls)): axum::extract::State<(Arc<AtomicUsize>, Arc<AtomicUsize>)>,
    headers: HeaderMap,
) -> axum::response::Response {
    assert_eq!(headers[header::COOKIE], "SID=synthetic-session");
    calls.fetch_add(1, Ordering::SeqCst);
    let location = match mode.load(Ordering::SeqCst) {
        0 => "/ready",
        1 => "http://127.0.0.1:9/secret",
        2 => "/",
        4 => "//[invalid/",
        _ => "",
    };
    let mut response = axum::response::Response::builder().status(302);
    if !location.is_empty() {
        response = response.header(header::LOCATION, location);
    } else if mode.load(Ordering::SeqCst) == 5 {
        response = response.header(
            header::LOCATION,
            header::HeaderValue::from_bytes(b"\xff").unwrap(),
        );
    }
    response.body(Body::empty()).unwrap()
}
