#[cfg(test)]
mod bootstrap_tests;
mod protocol;
mod temporary;
#[cfg(test)]
mod tests;
mod upload;

use crate::notebooklm_profiles::{LocalNotebookLMProfiles, ProfileError};
use flashcards_domain::Flashcard;
use flashcards_domain::identity::UserId;
use flashcards_services::{
    generation_options::GenerationOptions,
    notebooklm::{Artifact, ArtifactStatus, Notebook, NotebookLMGateway},
};
use reqwest::{Client, Response, StatusCode, cookie::CookieStore, header, redirect::Policy};
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    error::Error,
    fmt,
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};

const ORIGIN: &str = "https://notebook.google.com";
const RPC_PATH: &str = "/_/LabsTailwindUi/data/batchexecute";
const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
type CleanupTask = Result<tokio::task::JoinHandle<Result<(), NotebookLMError>>, NotebookLMError>;
type CleanupTasks = Arc<Mutex<Vec<CleanupTask>>>;

struct PendingCleanup {
    registry: CleanupTasks,
    tasks: VecDeque<CleanupTask>,
    failed: bool,
}

impl Drop for PendingCleanup {
    fn drop(&mut self) {
        if self.tasks.is_empty() && !self.failed {
            return;
        }
        let mut registry = self
            .registry
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if self.failed {
            registry.push(Err(NotebookLMError::SourceFailed));
        }
        registry.extend(self.tasks.drain(..));
    }
}

fn check_redirect_hop(hop: usize) -> Result<(), NotebookLMError> {
    if hop == 5 {
        return Err(NotebookLMError::Authentication);
    }
    Ok(())
}

fn local_cookie_host(host: &str) -> &str {
    if host == "notebook.google.com" {
        "notebooklm.google.com"
    } else {
        host
    }
}

async fn await_cleanup_task(task: &mut CleanupTask, deadline: tokio::time::Instant) -> bool {
    let Ok(task) = task else {
        return false;
    };
    match tokio::time::timeout_at(deadline, &mut *task).await {
        Ok(Ok(Ok(()))) => true,
        Ok(_) => false,
        Err(_) => {
            task.abort();
            let _ = task.await;
            false
        }
    }
}

async fn wait_until_ready<F: Future<Output = Result<bool, NotebookLMError>>>(
    timeout: Duration,
    poll: impl FnMut() -> F,
) -> Result<(), NotebookLMError> {
    tokio::time::timeout(timeout, poll_ready(poll))
        .await
        .map_err(NotebookLMError::Timeout)?
}

async fn drain_cleanup(tasks: &CleanupTasks) -> bool {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(40);
    let mut pending = PendingCleanup {
        registry: tasks.clone(),
        tasks: VecDeque::new(),
        failed: false,
    };
    loop {
        let available = std::mem::take(
            &mut *tasks
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner),
        );
        pending.tasks.extend(available);
        if pending.tasks.is_empty() {
            let complete = !pending.failed;
            pending.failed = false;
            return complete;
        }
        while let Some(task) = pending.tasks.front_mut() {
            pending.failed |= !await_cleanup_task(task, deadline).await;
            pending.tasks.pop_front();
        }
    }
}

async fn poll_ready<F: Future<Output = Result<bool, NotebookLMError>>>(
    mut poll: impl FnMut() -> F,
) -> Result<(), NotebookLMError> {
    loop {
        if poll().await? {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

#[derive(Debug)]
pub enum NotebookLMError {
    Authentication,
    InvalidInput,
    Io(std::io::Error),
    Profile(ProfileError),
    ProfileWorker(tokio::task::JoinError),
    Http(reqwest::Error),
    Status(StatusCode),
    Json(serde_json::Error),
    Schema(&'static str),
    ResponseTooLarge,
    RpcFailure,
    GenerationFailed,
    SourceFailed,
    Upload {
        cause: Box<Self>,
        cleanup_failed: bool,
    },
    Timeout(tokio::time::error::Elapsed),
}

impl fmt::Display for NotebookLMError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Authentication => formatter.write_str("NotebookLM login required"),
            Self::InvalidInput => formatter.write_str("Invalid NotebookLM input"),
            Self::Io(_) => formatter.write_str("Unable to read local NotebookLM profile"),
            Self::Profile(_) => formatter.write_str("Unable to access local NotebookLM profile"),
            Self::ProfileWorker(_) => formatter.write_str("Local NotebookLM profile worker failed"),
            Self::Http(_) | Self::Status(_) => formatter.write_str("NotebookLM request failed"),
            Self::Json(_) => formatter.write_str("Invalid NotebookLM JSON response"),
            Self::Schema(operation) => {
                write!(formatter, "Unexpected NotebookLM response: {operation}")
            }
            Self::ResponseTooLarge => formatter.write_str("NotebookLM response exceeds size limit"),
            Self::RpcFailure => formatter.write_str("NotebookLM refused the operation"),
            Self::GenerationFailed => formatter.write_str("NotebookLM flashcard generation failed"),
            Self::SourceFailed => formatter.write_str("NotebookLM source processing failed"),
            Self::Upload {
                cleanup_failed: true,
                ..
            } => formatter
                .write_str("NotebookLM upload failed; remote cleanup could not be confirmed"),
            Self::Upload { .. } => formatter.write_str("NotebookLM upload failed"),
            Self::Timeout(_) => formatter.write_str("NotebookLM generation timed out"),
        }
    }
}

impl Error for NotebookLMError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Http(error) => Some(error),
            Self::Io(error) => Some(error),
            Self::Profile(error) => Some(error),
            Self::ProfileWorker(error) => Some(error),
            Self::Json(error) => Some(error),
            Self::Timeout(error) => Some(error),
            Self::Upload { cause, .. } => Some(cause.as_ref()),
            _ => None,
        }
    }
}

impl From<reqwest::Error> for NotebookLMError {
    fn from(error: reqwest::Error) -> Self {
        Self::Http(error.without_url())
    }
}

impl From<serde_json::Error> for NotebookLMError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

fn trusted_bootstrap_url(url: &reqwest::Url, origin: &str) -> bool {
    let trusted = if origin == ORIGIN {
        url.scheme() == "https"
            && url.port_or_known_default() == Some(443)
            && matches!(
                url.host_str(),
                Some("notebook.google.com" | "notebooklm.google.com" | "accounts.google.com")
            )
    } else {
        url.origin().ascii_serialization() == origin
    };
    trusted
        && url.username().is_empty()
        && url.password().is_none()
        && url.fragment().is_none()
        && url.as_str().len() <= 16_384
}

async fn bootstrap(
    http: &Client,
    origin: &str,
    credentials: &protocol::Credentials,
) -> Result<String, NotebookLMError> {
    let mut url =
        reqwest::Url::parse(&format!("{origin}/")).map_err(|_| NotebookLMError::InvalidInput)?;
    url.query_pairs_mut()
        .append_pair("authuser", &credentials.authuser());
    for hop in 0..=5 {
        if !trusted_bootstrap_url(&url, origin) {
            tracing::debug!(
                host = url.host_str().unwrap_or_default(),
                scheme = url.scheme(),
                fragment_present = url.fragment().is_some(),
                "NotebookLM bootstrap target refused"
            );
            return Err(NotebookLMError::Authentication);
        }
        let mut request = http.get(url.clone());
        if origin != ORIGIN {
            request = request.header(header::COOKIE, credentials.header(url.path())?);
        }
        let response = request.send().await?;
        tracing::debug!(
            hop,
            host = url.host_str().unwrap_or_default(),
            status = response.status().as_u16(),
            "NotebookLM bootstrap response"
        );
        if response.status().is_redirection() {
            check_redirect_hop(hop)?;
            let location = response
                .headers()
                .get(header::LOCATION)
                .and_then(|value| value.to_str().ok())
                .ok_or(NotebookLMError::Authentication)?;
            url = url
                .join(location)
                .map_err(|_| NotebookLMError::Authentication)?;
            continue;
        }
        if url.origin().ascii_serialization() != origin {
            return Err(NotebookLMError::Authentication);
        }
        return bounded_response(response).await;
    }
    unreachable!("bounded bootstrap loop returns on its final hop")
}

#[derive(Clone)]
pub struct NotebookLMClient {
    http: Client,
    origin: String,
    credentials: Arc<protocol::Credentials>,
    cookies: Arc<reqwest::cookie::Jar>,
    csrf: String,
    session: String,
    build: String,
    request_id: Arc<AtomicU64>,
    cleanup_tasks: CleanupTasks,
}

impl NotebookLMClient {
    /// # Errors
    /// Propagates local profile loading, credential validation, and provider bootstrap failures.
    pub async fn for_user(
        profiles: &LocalNotebookLMProfiles,
        user: &UserId,
    ) -> Result<Self, NotebookLMError> {
        let profiles = profiles.clone();
        let user = user.clone();
        let raw = tokio::task::spawn_blocking(move || profiles.load(&user))
            .await
            .map_err(NotebookLMError::ProfileWorker)?
            .map_err(NotebookLMError::Profile)?
            .ok_or(NotebookLMError::Authentication)?;
        Self::from_storage_state(&raw).await
    }
    /// # Errors
    /// Propagates bounded credential-file reading, credential validation, and bootstrap failures.
    pub async fn from_storage_file(path: &Path) -> Result<Self, NotebookLMError> {
        let raw = read_storage_file(path).await?;
        Self::from_storage_state(&raw).await
    }

    /// # Errors
    /// Rejects invalid credentials and propagates provider bootstrap failures.
    pub async fn from_storage_state(raw: &str) -> Result<Self, NotebookLMError> {
        Self::connect(raw, ORIGIN).await
    }

    async fn connect(raw: &str, origin: &str) -> Result<Self, NotebookLMError> {
        let credentials = protocol::Credentials::parse(raw)?;
        let cookies = Arc::new(credentials.cookie_jar()?);
        let http = Client::builder()
            .cookie_provider(cookies.clone())
            .timeout(Duration::from_secs(30))
            .redirect(Policy::none())
            .retry(reqwest::retry::never())
            .build()?;
        let html = tokio::time::timeout(
            Duration::from_secs(30),
            bootstrap(&http, origin, &credentials),
        )
        .await
        .map_err(NotebookLMError::Timeout)??;
        let csrf = protocol::page_token(&html, "SNlM0e")?;
        let session = protocol::page_token(&html, "FdrFJe")?;
        let build = protocol::page_token(&html, "cfb2h")?;
        Ok(Self {
            http,
            origin: origin.to_owned(),
            credentials: Arc::new(credentials),
            cookies,
            csrf,
            session,
            build,
            request_id: Arc::new(AtomicU64::new(1)),
            cleanup_tasks: Arc::new(Mutex::new(Vec::new())),
        })
    }

    fn cookie_header(
        &self,
        path: &str,
        host: &str,
    ) -> Result<header::HeaderValue, NotebookLMError> {
        if self.origin != ORIGIN {
            return self
                .credentials
                .header_for_host(path, local_cookie_host(host));
        }
        let url = reqwest::Url::parse(&format!("https://{host}{path}"))
            .map_err(|_| NotebookLMError::InvalidInput)?;
        let mut header = self
            .cookies
            .cookies(&url)
            .ok_or(NotebookLMError::Authentication)?;
        header.set_sensitive(true);
        Ok(header)
    }

    pub async fn finish_cleanup(&self) -> bool {
        let complete = drain_cleanup(&self.cleanup_tasks).await;
        if !complete {
            tracing::warn!("Temporary NotebookLM cleanup could not be confirmed");
        }
        complete
    }

    async fn source_is_ready(&self, notebook: &str, source: &str) -> Result<bool, NotebookLMError> {
        let result = self
            .rpc(
                "rLM1Ne",
                &json!([notebook, null, protocol::client_options(false), null, 0]),
                Some(notebook),
                "en",
            )
            .await?;
        match protocol::source_status(&result, source)? {
            Some(ArtifactStatus::Ready) => Ok(true),
            Some(ArtifactStatus::Failed) => Err(NotebookLMError::SourceFailed),
            _ => Ok(false),
        }
    }

    async fn artifact_is_ready(
        &self,
        notebook: &str,
        artifact: &str,
    ) -> Result<bool, NotebookLMError> {
        let status = self
            .flashcard_artifacts(notebook)
            .await?
            .into_iter()
            .find(|item| item.id == artifact)
            .map(|item| item.status);
        match status {
            Some(ArtifactStatus::Ready) => Ok(true),
            Some(ArtifactStatus::Failed) => Err(NotebookLMError::GenerationFailed),
            _ => Ok(false),
        }
    }

    async fn rpc(
        &self,
        method: &str,
        params: &Value,
        notebook: Option<&str>,
        language: &str,
    ) -> Result<Value, NotebookLMError> {
        let response = self
            .rpc_request(method, params, notebook, language)?
            .send()
            .await?;
        protocol::decode_rpc(&bounded_response(response).await?, method)
    }

    fn rpc_request(
        &self,
        method: &str,
        params: &Value,
        notebook: Option<&str>,
        language: &str,
    ) -> Result<reqwest::RequestBuilder, NotebookLMError> {
        let source_path = notebook.map_or_else(|| "/".to_owned(), |id| format!("/notebook/{id}"));
        let encoded = serde_json::to_string(&json!([[[
            method,
            serde_json::to_string(params)?,
            null,
            "generic"
        ]]]))?;
        Ok(self
            .http
            .post(format!("{}{RPC_PATH}", self.origin))
            .header(
                header::COOKIE,
                self.cookie_header(RPC_PATH, "notebook.google.com")?,
            )
            .header(header::ORIGIN, &self.origin)
            .header(header::REFERER, format!("{}{source_path}", self.origin))
            .header("x-goog-authuser", self.credentials.authuser_header()?)
            .query(&[
                ("rpcids", method.to_owned()),
                ("source-path", source_path),
                ("f.sid", self.session.clone()),
                ("bl", self.build.clone()),
                ("hl", language.to_owned()),
                ("rt", "c".to_owned()),
                ("authuser", self.credentials.authuser()),
                (
                    "_reqid",
                    self.request_id
                        .fetch_add(100_000, Ordering::Relaxed)
                        .to_string(),
                ),
            ])
            .form(&[("f.req", encoded), ("at", self.csrf.clone())]))
    }

    async fn flashcard_artifacts(&self, notebook: &str) -> Result<Vec<Artifact>, NotebookLMError> {
        let result = self
            .rpc(
                "gArtLc",
                &json!([
                    [2],
                    notebook,
                    "NOT artifact.status = \"ARTIFACT_STATUS_SUGGESTED\""
                ]),
                Some(notebook),
                "en",
            )
            .await?;
        if result.is_null() {
            return Ok(Vec::new());
        }
        let rows = result
            .as_array()
            .ok_or(NotebookLMError::Schema("artifact list"))?;
        let rows = if rows.len() == 1
            && rows[0]
                .as_array()
                .is_some_and(|inner| inner.is_empty() || inner[0].is_array())
        {
            rows[0]
                .as_array()
                .ok_or(NotebookLMError::Schema("artifact list"))?
        } else {
            rows
        };
        rows.iter()
            .filter(|row| {
                row.get(2).and_then(Value::as_u64) == Some(4)
                    && row.pointer("/9/1/0").and_then(Value::as_u64) == Some(1)
            })
            .map(protocol::artifact)
            .collect()
    }
}

pub(crate) fn validate_storage_state(raw: &str) -> Result<(), NotebookLMError> {
    let credentials = protocol::Credentials::parse(raw)?;
    credentials.cookie_jar()?;
    credentials.header("/")?;
    Ok(())
}

async fn read_storage_file(path: &Path) -> Result<String, NotebookLMError> {
    use tokio::io::AsyncReadExt;
    let metadata = tokio::fs::symlink_metadata(path)
        .await
        .map_err(NotebookLMError::Io)?;
    if !metadata.is_file() {
        return Err(NotebookLMError::Authentication);
    }
    if metadata.len() > 4 * 1024 * 1024 {
        return Err(NotebookLMError::ResponseTooLarge);
    }
    let file = tokio::fs::OpenOptions::from(crate::document_files::readonly_file_options())
        .open(path)
        .await
        .map_err(NotebookLMError::Io)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        let opened = file.metadata().await.map_err(NotebookLMError::Io)?;
        if opened.dev() != metadata.dev() || opened.ino() != metadata.ino() {
            return Err(NotebookLMError::Authentication);
        }
        file.set_permissions(std::fs::Permissions::from_mode(0o600))
            .await
            .map_err(NotebookLMError::Io)?;
    }
    let mut raw = String::new();
    file.take(4 * 1024 * 1024 + 1)
        .read_to_string(&mut raw)
        .await
        .map_err(NotebookLMError::Io)?;
    if raw.len() > 4 * 1024 * 1024 {
        return Err(NotebookLMError::ResponseTooLarge);
    }
    Ok(raw)
}

async fn bounded_response(mut response: Response) -> Result<String, NotebookLMError> {
    let status = response.status();
    if matches!(status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) || status.is_redirection()
    {
        return Err(NotebookLMError::Authentication);
    }
    if !status.is_success() {
        return Err(NotebookLMError::Status(status));
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_RESPONSE_BYTES as u64)
    {
        return Err(NotebookLMError::ResponseTooLarge);
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        if chunk.len() > MAX_RESPONSE_BYTES - bytes.len() {
            return Err(NotebookLMError::ResponseTooLarge);
        }
        bytes.extend_from_slice(&chunk);
    }
    String::from_utf8(bytes).map_err(|_| NotebookLMError::Schema("UTF-8 response"))
}

impl NotebookLMGateway for NotebookLMClient {
    type Error = NotebookLMError;

    async fn add_file_source(&self, notebook: &str, path: &Path) -> Result<String, Self::Error> {
        self.upload_file(notebook, path).await
    }

    async fn list_notebooks(&self) -> Result<Vec<Notebook>, Self::Error> {
        let result = self
            .rpc("wXbhsf", &json!([null, 1, null, [2]]), None, "en")
            .await?;
        if result.is_null() {
            return Ok(Vec::new());
        }
        result
            .get(0)
            .and_then(Value::as_array)
            .ok_or(NotebookLMError::Schema("notebook list"))?
            .iter()
            .map(protocol::notebook)
            .collect()
    }

    async fn create_notebook(&self, title: &str) -> Result<Notebook, Self::Error> {
        if title.trim().is_empty() || title.chars().count() > 1000 {
            return Err(NotebookLMError::InvalidInput);
        }
        let result = self
            .rpc(
                "CCqFvf",
                &json!([title, null, null, protocol::client_options(false)]),
                None,
                "en",
            )
            .await?;
        protocol::notebook(&result)
    }

    async fn add_text_source(
        &self,
        notebook: &str,
        title: &str,
        text: &str,
    ) -> Result<String, Self::Error> {
        protocol::identifier(notebook)?;
        if title.trim().is_empty()
            || title.chars().count() > 1000
            || text.trim().is_empty()
            || text.len() > MAX_RESPONSE_BYTES
        {
            return Err(NotebookLMError::InvalidInput);
        }
        let result = self
            .rpc(
                "izAoDd",
                &json!([
                    [[
                        null,
                        [title, text],
                        null,
                        2,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        1
                    ]],
                    notebook,
                    protocol::client_options(false)
                ]),
                Some(notebook),
                "en",
            )
            .await?;
        protocol::source_identifier(&result)
    }

    async fn generate_flashcards(
        &self,
        notebook: &str,
        sources: &[String],
        prompt: &str,
        options: &GenerationOptions,
    ) -> Result<Artifact, Self::Error> {
        protocol::identifier(notebook)?;
        if sources.is_empty() || sources.len() > 300 || prompt.chars().count() > 100_000 {
            return Err(NotebookLMError::InvalidInput);
        }
        for source in sources {
            protocol::identifier(source)?;
        }
        let params = protocol::flashcard_params(notebook, sources, prompt, options);
        let result = self
            .rpc("R7cb6c", &params, Some(notebook), options.language())
            .await?;
        protocol::artifact(
            result
                .get(0)
                .ok_or(NotebookLMError::Schema("generated artifact"))?,
        )
    }

    async fn wait_for_source(
        &self,
        notebook: &str,
        source: &str,
        timeout: Duration,
    ) -> Result<(), Self::Error> {
        protocol::identifier(notebook)?;
        protocol::identifier(source)?;
        wait_until_ready(timeout, || self.source_is_ready(notebook, source)).await
    }

    async fn wait_for_artifact(
        &self,
        notebook: &str,
        artifact: &str,
        timeout: Duration,
    ) -> Result<(), Self::Error> {
        protocol::identifier(notebook)?;
        protocol::identifier(artifact)?;
        wait_until_ready(timeout, || self.artifact_is_ready(notebook, artifact)).await
    }

    async fn download_flashcards(
        &self,
        notebook: &str,
        artifact: &str,
    ) -> Result<Vec<Flashcard>, Self::Error> {
        protocol::identifier(notebook)?;
        protocol::identifier(artifact)?;
        let result = self
            .rpc("v9rmvd", &json!([artifact]), Some(notebook), "en")
            .await?;
        let html = result
            .pointer("/0/9/0")
            .and_then(Value::as_str)
            .ok_or(NotebookLMError::Schema("interactive flashcards"))?;
        protocol::flashcards(html)
    }

    async fn delete_notebook(&self, notebook: &str) -> Result<(), Self::Error> {
        protocol::identifier(notebook)?;
        self.rpc("WWINqb", &json!([[notebook], [2]]), None, "en")
            .await?;
        Ok(())
    }
}
