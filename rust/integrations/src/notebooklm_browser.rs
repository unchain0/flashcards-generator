use crate::{
    notebooklm::{NotebookLMClient, NotebookLMError, validate_storage_state},
    notebooklm_profiles::{LocalNotebookLMProfiles, ProfileError, read_private_file},
};
use flashcards_domain::identity::UserId;
use flashcards_services::notebooklm::NotebookLMGateway;
use futures_util::{SinkExt, StreamExt};
use reqwest::Url;
use serde_json::{Value, json};
use std::{
    error::Error,
    fmt,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::{
    net::TcpStream,
    process::{Child, Command},
};
use tokio_tungstenite::{
    WebSocketStream, client_async_with_config,
    tungstenite::{Message, protocol::WebSocketConfig},
};

const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);
const COMMAND_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_MESSAGE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug)]
pub enum BrowserLoginError {
    Unavailable,
    Closed,
    Protocol,
    Timeout,
    Io(std::io::Error),
    Profile(ProfileError),
    Worker(tokio::task::JoinError),
    Verification(NotebookLMError),
}

impl fmt::Display for BrowserLoginError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Unavailable => "Chrome or Chromium is required for NotebookLM login",
            Self::Closed => "NotebookLM login browser was closed",
            Self::Protocol => "Unable to communicate with NotebookLM login browser",
            Self::Timeout => "NotebookLM login timed out",
            Self::Io(_) => "Unable to start or stop NotebookLM login browser",
            Self::Profile(_) => "Unable to access local NotebookLM browser profile",
            Self::Worker(_) => "NotebookLM login profile worker failed",
            Self::Verification(_) => "Unable to verify NotebookLM browser session",
        })
    }
}

impl Error for BrowserLoginError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Profile(error) => Some(error),
            Self::Worker(error) => Some(error),
            Self::Verification(error) => Some(error),
            _ => None,
        }
    }
}

impl From<ProfileError> for BrowserLoginError {
    fn from(error: ProfileError) -> Self {
        Self::Profile(error)
    }
}
impl From<std::io::Error> for BrowserLoginError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Clone, Default)]
pub struct NotebookLMBrowserLogin {
    executable: Option<PathBuf>,
}

impl NotebookLMBrowserLogin {
    #[must_use]
    pub fn new(executable: Option<PathBuf>) -> Self {
        Self { executable }
    }

    /// # Errors
    /// Propagates browser discovery, profile locking, browser protocol, and verified credential persistence failures.
    pub async fn login(
        &self,
        profiles: &LocalNotebookLMProfiles,
        user: &UserId,
    ) -> Result<(), BrowserLoginError> {
        let profiles_copy = profiles.clone();
        let user_copy = user.clone();
        let (home, _lock) =
            tokio::task::spawn_blocking(move || profiles_copy.lock_browser(&user_copy))
                .await
                .map_err(BrowserLoginError::Worker)??;
        let executable = browser_executable(self.executable.as_deref())?;
        let endpoint_path = home.join("DevToolsActivePort");
        let previous = read_endpoint_file(endpoint_path.clone()).await?;
        let mut child =
            browser_command(&executable, &home, "https://notebook.google.com/").spawn()?;
        let result = tokio::time::timeout(LOGIN_TIMEOUT, async {
            let endpoint = wait_for_endpoint(&mut child, endpoint_path, previous).await?;
            let mut cdp = Cdp::connect(&endpoint).await?;
            let raw = capture(&mut cdp, verify_session).await?;
            let profiles = profiles.clone();
            let user = user.clone();
            tokio::task::spawn_blocking(move || profiles.save(&user, &raw))
                .await
                .map_err(BrowserLoginError::Worker)??;
            let _ = cdp.call("Browser.close", json!({})).await;
            Ok::<(), BrowserLoginError>(())
        })
        .await
        .unwrap_or(Err(BrowserLoginError::Timeout));
        stop_browser(&mut child, result.is_ok()).await?;
        result
    }
}

async fn verify_session(raw: &str) -> Result<(), NotebookLMError> {
    NotebookLMClient::from_storage_state(raw)
        .await?
        .list_notebooks()
        .await?;
    Ok(())
}

fn browser_executable(explicit: Option<&Path>) -> Result<PathBuf, BrowserLoginError> {
    if let Some(path) = explicit {
        return (path.is_absolute() && path.is_file())
            .then(|| path.to_owned())
            .ok_or(BrowserLoginError::Unavailable);
    }
    let search_path = std::env::var_os("PATH").ok_or(BrowserLoginError::Unavailable)?;
    let names = [
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "chrome.exe",
        "msedge.exe",
    ];
    std::env::split_paths(&search_path)
        .filter(|path| path.is_absolute())
        .flat_map(|directory| names.map(|name| directory.join(name)))
        .find(|path| path.is_file())
        .ok_or(BrowserLoginError::Unavailable)
}

fn browser_command(executable: &Path, home: &Path, target: &str) -> Command {
    let mut command = Command::new(executable);
    let mut profile_argument = std::ffi::OsString::from("--user-data-dir=");
    profile_argument.push(home);
    command
        .arg(profile_argument)
        .args([
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            target,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    command
}

async fn stop_browser(child: &mut Child, graceful: bool) -> Result<(), BrowserLoginError> {
    if graceful && let Ok(result) = tokio::time::timeout(Duration::from_secs(5), child.wait()).await
    {
        result?;
        return Ok(());
    }
    if child.try_wait()?.is_none() {
        child.start_kill()?;
        tokio::time::timeout(Duration::from_secs(5), child.wait())
            .await
            .map_err(|_| BrowserLoginError::Timeout)??;
    }
    Ok(())
}

async fn read_endpoint_file(path: PathBuf) -> Result<Option<String>, BrowserLoginError> {
    tokio::task::spawn_blocking(move || read_private_file(&path, 4096))
        .await
        .map_err(BrowserLoginError::Worker)?
        .map_err(Into::into)
}

async fn wait_for_endpoint(
    child: &mut Child,
    path: PathBuf,
    previous: Option<String>,
) -> Result<String, BrowserLoginError> {
    tokio::time::timeout(
        Duration::from_secs(30),
        poll_endpoint(child, &path, previous.as_deref()),
    )
    .await
    .map_err(|_| BrowserLoginError::Timeout)?
}

async fn poll_endpoint(
    child: &mut Child,
    path: &Path,
    previous: Option<&str>,
) -> Result<String, BrowserLoginError> {
    loop {
        if child.try_wait()?.is_some() {
            return Err(BrowserLoginError::Closed);
        }
        if let Some(raw) = read_endpoint_file(path.to_owned()).await?
            && previous != Some(&raw)
        {
            return endpoint_url(&raw);
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

fn endpoint_url(raw: &str) -> Result<String, BrowserLoginError> {
    let mut lines = raw.lines();
    let port = lines
        .next()
        .and_then(|line| line.parse::<u16>().ok())
        .filter(|port| *port != 0)
        .ok_or(BrowserLoginError::Protocol)?;
    let path = lines
        .next()
        .and_then(|line| line.strip_prefix("/devtools/browser/"))
        .filter(|id| {
            !id.is_empty()
                && id.len() <= 128
                && id
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        })
        .ok_or(BrowserLoginError::Protocol)?;
    if lines.next().is_some() {
        return Err(BrowserLoginError::Protocol);
    }
    Ok(format!("ws://127.0.0.1:{port}/devtools/browser/{path}"))
}

struct Cdp {
    socket: WebSocketStream<TcpStream>,
    request_id: u64,
}

impl Cdp {
    async fn connect(endpoint: &str) -> Result<Self, BrowserLoginError> {
        tokio::time::timeout(COMMAND_TIMEOUT, Self::connect_socket(endpoint))
            .await
            .map_err(|_| BrowserLoginError::Timeout)?
    }

    async fn connect_socket(endpoint: &str) -> Result<Self, BrowserLoginError> {
        let url = Url::parse(endpoint).map_err(|_| BrowserLoginError::Protocol)?;
        if url.scheme() != "ws"
            || url.host_str() != Some("127.0.0.1")
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(BrowserLoginError::Protocol);
        }
        let port = url.port().ok_or(BrowserLoginError::Protocol)?;
        let stream = TcpStream::connect((std::net::Ipv4Addr::LOCALHOST, port)).await?;
        let config = WebSocketConfig::default()
            .max_message_size(Some(MAX_MESSAGE_BYTES))
            .max_frame_size(Some(MAX_MESSAGE_BYTES));
        let (socket, _) = client_async_with_config(endpoint, stream, Some(config))
            .await
            .map_err(|_| BrowserLoginError::Protocol)?;
        Ok(Self {
            socket,
            request_id: 0,
        })
    }

    async fn call(&mut self, method: &str, params: Value) -> Result<Value, BrowserLoginError> {
        tokio::time::timeout(COMMAND_TIMEOUT, self.exchange(method, params))
            .await
            .map_err(|_| BrowserLoginError::Timeout)?
    }

    async fn exchange(&mut self, method: &str, params: Value) -> Result<Value, BrowserLoginError> {
        self.request_id += 1;
        let request = json!({"id":self.request_id,"method":method,"params":params});
        self.socket
            .send(Message::Text(request.to_string().into()))
            .await
            .map_err(|_| BrowserLoginError::Protocol)?;
        receive_command_response(&mut self.socket, self.request_id).await
    }
}

async fn receive_command_response(
    socket: &mut WebSocketStream<TcpStream>,
    request_id: u64,
) -> Result<Value, BrowserLoginError> {
    for _ in 0..128 {
        let message = socket
            .next()
            .await
            .ok_or(BrowserLoginError::Closed)?
            .map_err(|_| BrowserLoginError::Protocol)?;
        if let Some(response) = command_response(message, request_id)? {
            return Ok(response);
        }
    }
    Err(BrowserLoginError::Protocol)
}

fn command_response(message: Message, request_id: u64) -> Result<Option<Value>, BrowserLoginError> {
    let raw = match message {
        Message::Text(raw) => raw,
        Message::Ping(_) | Message::Pong(_) => return Ok(None),
        Message::Close(_) => return Err(BrowserLoginError::Closed),
        _ => return Err(BrowserLoginError::Protocol),
    };
    let response: Value = serde_json::from_str(&raw).map_err(|_| BrowserLoginError::Protocol)?;
    if !response.is_object() {
        return Err(BrowserLoginError::Protocol);
    }
    if response.get("id").is_none() {
        return Ok(None);
    }
    if response["id"].as_u64() != Some(request_id) || response.get("error").is_some() {
        return Err(BrowserLoginError::Protocol);
    }
    response
        .get("result")
        .filter(|result| result.is_object())
        .cloned()
        .map(Some)
        .ok_or(BrowserLoginError::Protocol)
}

async fn capture(
    cdp: &mut Cdp,
    verify: impl AsyncFn(&str) -> Result<(), NotebookLMError>,
) -> Result<String, BrowserLoginError> {
    loop {
        let targets = cdp.call("Target.getTargets", json!({})).await?;
        let rows = targets["targetInfos"]
            .as_array()
            .filter(|rows| rows.len() <= 128)
            .ok_or(BrowserLoginError::Protocol)?;
        if let Some(raw) = capture_targets(cdp, rows, &verify).await? {
            return Ok(raw);
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

async fn capture_targets(
    cdp: &mut Cdp,
    rows: &[Value],
    verify: &impl AsyncFn(&str) -> Result<(), NotebookLMError>,
) -> Result<Option<String>, BrowserLoginError> {
    for target in rows {
        let Some(authuser) = notebooklm_account(target) else {
            continue;
        };
        let cookies = cdp.call("Storage.getCookies", json!({})).await?;
        let Some(raw) = storage_state(&cookies, authuser)? else {
            continue;
        };
        match verify(&raw).await {
            Ok(()) => return Ok(Some(raw)),
            Err(NotebookLMError::Authentication) => {}
            Err(error) => return Err(BrowserLoginError::Verification(error)),
        }
    }
    Ok(None)
}

fn notebooklm_account(target: &Value) -> Option<u16> {
    if target["type"] != "page" {
        return None;
    }
    let url = Url::parse(target["url"].as_str()?).ok()?;
    if url.scheme() != "https"
        || !matches!(
            url.host_str(),
            Some("notebook.google.com" | "notebooklm.google.com")
        )
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return None;
    }
    let accounts: Vec<_> = url
        .query_pairs()
        .filter(|(name, _)| name == "authuser")
        .collect();
    match accounts.as_slice() {
        [] => Some(0),
        [(_, value)] => value.parse().ok(),
        _ => None,
    }
}

fn storage_state(result: &Value, authuser: u16) -> Result<Option<String>, BrowserLoginError> {
    let cookies = result["cookies"]
        .as_array()
        .ok_or(BrowserLoginError::Protocol)?;
    let cookies: Vec<_> = cookies
        .iter()
        .filter(|cookie| {
            cookie["domain"].as_str().is_some_and(|domain| {
                matches!(
                    domain.strip_prefix('.').unwrap_or(domain),
                    "google.com"
                        | "notebook.google.com"
                        | "notebooklm.google.com"
                        | "accounts.google.com"
                )
            })
        })
        .cloned()
        .collect();
    let raw = json!({"cookies":cookies,"origins":[],"authuser":authuser}).to_string();
    match validate_storage_state(&raw) {
        Ok(()) => Ok(Some(raw)),
        Err(NotebookLMError::Authentication) => Ok(None),
        Err(_) => Err(BrowserLoginError::Protocol),
    }
}

#[cfg(test)]
mod tests;
