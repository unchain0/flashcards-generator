use super::{CleanupTasks, NotebookLMClient, NotebookLMError, ORIGIN, bounded_response, protocol};
use reqwest::{Body, RequestBuilder, Url, header};
use serde_json::{Value, json};
use std::{collections::HashSet, path::Path, time::Duration};
use tokio::io::AsyncReadExt;
use tokio_util::io::ReaderStream;

const UPLOAD_PATH: &str = "/upload/_/";
const MAX_FILE_BYTES: u64 = 512 * 1024 * 1024;
#[cfg(test)]
mod tests;

impl NotebookLMClient {
    pub(super) async fn upload_file(
        &self,
        notebook: &str,
        path: &Path,
    ) -> Result<String, NotebookLMError> {
        protocol::identifier(notebook)?;
        let upload = open_upload(path).await?;
        let source = self.register_upload(notebook, &upload.name).await?;
        let remove = self
            .rpc_request("tGMBJ", &json!([[[source]]]), Some(notebook), "en")?
            .timeout(Duration::from_secs(5));
        let mut cleanup = UploadCleanup {
            remove: Some(remove),
            cancel: None,
            tasks: Some(self.cleanup_tasks.clone()),
        };
        match self
            .transfer_upload(notebook, &source, upload, &mut cleanup)
            .await
        {
            Ok(()) => {
                cleanup.remove = None;
                cleanup.cancel = None;
                Ok(source)
            }
            Err(cause) => {
                let cleanup_failed = cleanup.run().await;
                Err(NotebookLMError::Upload {
                    cause: Box::new(cause),
                    cleanup_failed,
                })
            }
        }
    }

    async fn register_upload(&self, notebook: &str, name: &str) -> Result<String, NotebookLMError> {
        let baseline = self
            .rpc(
                "rLM1Ne",
                &json!([notebook, null, protocol::client_options(false), null, 0]),
                Some(notebook),
                "en",
            )
            .await?;
        let rows = baseline
            .pointer("/0/1")
            .ok_or(NotebookLMError::Schema("upload source baseline"))?;
        let existing = if rows.is_null() {
            HashSet::new()
        } else {
            rows.as_array()
                .ok_or(NotebookLMError::Schema("upload source baseline"))?
                .iter()
                .map(protocol::source_identifier)
                .collect::<Result<HashSet<_>, _>>()?
        };
        let registration = self
            .rpc(
                "o4cbdc",
                &json!([[[name]], notebook, protocol::client_options(false)]),
                Some(notebook),
                "en",
            )
            .await?;
        let source = registered_id(&registration, name)?;
        if existing.contains(&source) {
            return Err(NotebookLMError::Schema("ambiguous upload registration"));
        }
        Ok(source)
    }

    async fn transfer_upload(
        &self,
        notebook: &str,
        source: &str,
        upload: UploadFile,
        cleanup: &mut UploadCleanup,
    ) -> Result<(), NotebookLMError> {
        let target = self.start_upload(notebook, source, &upload).await?;
        let origin = target.origin().ascii_serialization();
        let host = if self.origin == ORIGIN {
            target.host_str().ok_or(NotebookLMError::InvalidInput)?
        } else {
            "notebooklm.google.com"
        };
        let headers = self.cookie_header(target.path(), host)?;
        let request = self
            .http
            .post(target)
            .header(header::COOKIE, headers)
            .header(
                header::CONTENT_TYPE,
                "application/x-www-form-urlencoded;charset=utf-8",
            )
            .header(header::ORIGIN, &origin)
            .header(header::REFERER, format!("{origin}/"))
            .header("x-goog-authuser", self.credentials.authuser_header()?);
        cleanup.cancel = Some(
            request
                .try_clone()
                .ok_or(NotebookLMError::InvalidInput)?
                .header("x-goog-upload-command", "cancel")
                .timeout(Duration::from_secs(5)),
        );
        let response = request
            .header("x-goog-upload-command", "upload, finalize")
            .header("x-goog-upload-offset", "0")
            .header(header::CONTENT_LENGTH, upload.length)
            .timeout(Duration::from_secs(300))
            .body(Body::wrap_stream(ReaderStream::with_capacity(
                upload.file.take(upload.length),
                65_536,
            )))
            .send()
            .await?;
        bounded_response(response).await?;
        Ok(())
    }

    async fn start_upload(
        &self,
        notebook: &str,
        source: &str,
        upload: &UploadFile,
    ) -> Result<Url, NotebookLMError> {
        let start = self
            .http
            .post(format!("{}{UPLOAD_PATH}", self.origin))
            .query(&[("authuser", self.credentials.authuser())])
            .header(
                header::COOKIE,
                self.cookie_header(UPLOAD_PATH, "notebook.google.com")?,
            )
            .header(
                header::CONTENT_TYPE,
                "application/x-www-form-urlencoded;charset=UTF-8",
            )
            .header(header::ORIGIN, &self.origin)
            .header(header::REFERER, format!("{}/", self.origin))
            .header("x-goog-authuser", self.credentials.authuser_header()?)
            .header("x-goog-upload-command", "start")
            .header("x-goog-upload-protocol", "resumable")
            .header("x-goog-upload-header-content-length", upload.length)
            .header("x-goog-upload-header-content-type", upload.content_type)
            .body(
                json!({"PROJECT_ID":notebook,"SOURCE_NAME":upload.name,"SOURCE_ID":source})
                    .to_string(),
            )
            .send()
            .await?;
        let raw = start
            .headers()
            .get("x-goog-upload-url")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        bounded_response(start).await?;
        validated_upload_url(
            raw.as_deref()
                .ok_or(NotebookLMError::Schema("upload session URL"))?,
            &self.origin,
        )
    }
}

struct UploadFile {
    name: String,
    content_type: &'static str,
    length: u64,
    file: tokio::fs::File,
}

async fn open_upload(path: &Path) -> Result<UploadFile, NotebookLMError> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| name.len() <= 255 && !name.chars().any(char::is_control))
        .ok_or(NotebookLMError::InvalidInput)?;
    let content_type = match path
        .extension()
        .and_then(|suffix| suffix.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("pdf") => "application/pdf",
        Some("pptx") => "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        _ => return Err(NotebookLMError::InvalidInput),
    };
    let metadata = tokio::fs::symlink_metadata(path)
        .await
        .map_err(NotebookLMError::Io)?;
    if !metadata.is_file() || metadata.len() == 0 {
        return Err(NotebookLMError::InvalidInput);
    }
    if metadata.len() > MAX_FILE_BYTES {
        return Err(NotebookLMError::ResponseTooLarge);
    }
    let file = tokio::fs::OpenOptions::from(crate::document_files::readonly_file_options())
        .open(path)
        .await
        .map_err(NotebookLMError::Io)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let opened = file.metadata().await.map_err(NotebookLMError::Io)?;
        if opened.dev() != metadata.dev() || opened.ino() != metadata.ino() {
            return Err(NotebookLMError::InvalidInput);
        }
    }
    Ok(UploadFile {
        name: name.to_owned(),
        content_type,
        length: metadata.len(),
        file,
    })
}

struct UploadCleanup {
    remove: Option<RequestBuilder>,
    cancel: Option<RequestBuilder>,
    tasks: Option<CleanupTasks>,
}

impl UploadCleanup {
    async fn run(&mut self) -> bool {
        let mut failed = false;
        for (rpc, slot) in [(false, &mut self.cancel), (true, &mut self.remove)] {
            failed |= cleanup_request(slot.as_ref(), rpc).await.is_err();
            *slot = None;
        }
        failed
    }
}

impl Drop for UploadCleanup {
    fn drop(&mut self) {
        if self.cancel.is_none() && self.remove.is_none() {
            return;
        }
        let Some(tasks) = self.tasks.take() else {
            return;
        };
        let mut cleanup = Self {
            remove: self.remove.take(),
            cancel: self.cancel.take(),
            tasks: None,
        };
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::warn!("NotebookLM upload cleanup unavailable after runtime shutdown");
            tasks
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .push(Err(NotebookLMError::SourceFailed));
            cleanup.remove = None;
            cleanup.cancel = None;
            return;
        };
        let task = runtime.spawn(run_dropped_cleanup(cleanup));
        tasks
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(Ok(task));
    }
}

async fn cleanup_request(
    request: Option<&RequestBuilder>,
    rpc: bool,
) -> Result<(), NotebookLMError> {
    let Some(request) = request else {
        return Ok(());
    };
    let request = request.try_clone().ok_or(NotebookLMError::InvalidInput)?;
    let raw = bounded_response(request.send().await?).await?;
    if rpc {
        protocol::decode_rpc(&raw, "tGMBJ")?;
    }
    Ok(())
}

async fn run_dropped_cleanup(mut cleanup: UploadCleanup) -> Result<(), NotebookLMError> {
    if cleanup.run().await {
        tracing::warn!("NotebookLM upload cancellation cleanup could not be confirmed");
        return Err(NotebookLMError::SourceFailed);
    }
    Ok(())
}

fn validated_upload_url(raw: &str, origin: &str) -> Result<Url, NotebookLMError> {
    let invalid = || NotebookLMError::Schema("untrusted upload URL");
    if raw.len() > 16_384
        || raw.split_once("://").is_none_or(|(_, authority)| {
            authority
                .split('/')
                .next()
                .is_none_or(|authority| authority.contains('@'))
        })
    {
        return Err(invalid());
    }
    let url = Url::parse(raw).map_err(|_| invalid())?;
    let trusted = if origin == ORIGIN {
        url.scheme() == "https"
            && matches!(
                url.host_str(),
                Some("notebooklm.google.com" | "notebook.google.com")
            )
            && url.port_or_known_default() == Some(443)
    } else {
        url.origin().ascii_serialization() == origin
    };
    if !trusted
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.path() != UPLOAD_PATH
    {
        return Err(invalid());
    }
    let upload_ids = url
        .query_pairs()
        .filter(|(name, _)| name.eq_ignore_ascii_case("upload_id"))
        .map(|(_, value)| value.into_owned())
        .collect::<Vec<_>>();
    if upload_ids.len() != 1 || upload_ids[0].is_empty() {
        return Err(invalid());
    }
    Ok(url)
}

fn registered_id(result: &Value, filename: &str) -> Result<String, NotebookLMError> {
    let mut value = result;
    for _ in 0..8 {
        if let Some(object) = value.as_object()
            && let Some(id) = registered_object(object, filename)?
        {
            return Ok(id);
        }
        if value.is_object() {
            break;
        }
        if let Some(inner) = registration_inner(value) {
            value = inner;
            continue;
        }
        if value != filename
            && let Some(id) = value.as_str()
        {
            return protocol::identifier(id).map(str::to_owned);
        }
        break;
    }
    let mut candidates = HashSet::new();
    registered_rows(result, filename, 0, &mut candidates)?;
    if candidates.len() == 1 {
        return candidates
            .into_iter()
            .next()
            .ok_or(NotebookLMError::Schema("upload source registration"));
    }
    Err(NotebookLMError::Schema("upload source registration"))
}

fn registered_object(
    object: &serde_json::Map<String, Value>,
    filename: &str,
) -> Result<Option<String>, NotebookLMError> {
    if object
        .get("SOURCE_NAME")
        .and_then(Value::as_str)
        .is_some_and(|name| name != filename)
    {
        return Ok(None);
    }
    let candidates = ["SOURCE_ID", "source_id", "sourceId"]
        .iter()
        .filter_map(|name| object.get(*name).and_then(Value::as_str))
        .collect::<HashSet<_>>();
    if candidates.len() != 1 {
        return Ok(None);
    }
    let candidate = candidates
        .into_iter()
        .next()
        .ok_or(NotebookLMError::InvalidInput)?;
    Ok(Some(protocol::identifier(candidate)?.to_owned()))
}

fn registration_inner(value: &Value) -> Option<&Value> {
    match value.as_array()?.as_slice() {
        [inner] | [Value::Null, inner] => Some(inner),
        _ => None,
    }
}

fn registered_rows(
    value: &Value,
    filename: &str,
    depth: usize,
    candidates: &mut HashSet<String>,
) -> Result<(), NotebookLMError> {
    let Some(rows) = value.as_array() else {
        return Ok(());
    };
    if depth > 8 || rows.len() > 256 {
        return Err(NotebookLMError::Schema("upload source registration"));
    }
    if rows.get(1).and_then(Value::as_str) == Some(filename) {
        candidates.insert(protocol::source_identifier(value)?);
        return Ok(());
    }
    for row in rows {
        registered_rows(row, filename, depth + 1, candidates)?;
    }
    Ok(())
}
