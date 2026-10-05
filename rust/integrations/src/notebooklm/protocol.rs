use super::{MAX_RESPONSE_BYTES, NotebookLMError};
use flashcards_domain::Flashcard;
use flashcards_services::{
    generation_options::{Difficulty, GenerationOptions, Quantity},
    notebooklm::{Artifact, ArtifactStatus, Notebook},
};
use regex::Regex;
use reqwest::header::HeaderValue;
use reqwest::{Url, cookie::Jar};
use serde::Deserialize;
use serde_json::{Value, json};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Deserialize)]
pub(super) struct Credentials {
    cookies: Vec<StoredCookie>,
    #[serde(default)]
    authuser: u16,
    #[serde(default)]
    notebooklm: Option<ProfileMetadata>,
}

#[derive(Deserialize)]
struct ProfileMetadata {
    #[serde(default)]
    account: Option<AccountMetadata>,
}

#[derive(Deserialize)]
struct AccountMetadata {
    #[serde(default)]
    authuser: Option<u16>,
    #[serde(default)]
    email: Option<String>,
}

#[derive(Deserialize)]
struct StoredCookie {
    name: String,
    value: String,
    domain: String,
    #[serde(default = "root_path")]
    path: String,
    #[serde(default)]
    expires: Option<f64>,
}

fn root_path() -> String {
    "/".to_owned()
}

impl Credentials {
    pub fn authuser(&self) -> String {
        let account = self
            .notebooklm
            .as_ref()
            .and_then(|metadata| metadata.account.as_ref());
        if let Some(email) = account
            .and_then(|account| account.email.as_deref())
            .map(str::trim)
            .filter(|email| !email.is_empty())
        {
            return email.to_owned();
        }
        account
            .and_then(|account| account.authuser)
            .unwrap_or(self.authuser)
            .to_string()
    }

    pub fn authuser_header(&self) -> Result<HeaderValue, NotebookLMError> {
        let mut header =
            HeaderValue::from_str(&self.authuser()).map_err(|_| NotebookLMError::Authentication)?;
        header.set_sensitive(true);
        Ok(header)
    }

    pub fn cookie_jar(&self) -> Result<Jar, NotebookLMError> {
        let jar = Jar::default();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| NotebookLMError::Authentication)?
            .as_secs_f64();
        for cookie in &self.cookies {
            self.add_cookie(&jar, cookie, now)?;
        }
        Ok(jar)
    }

    fn add_cookie(
        &self,
        jar: &Jar,
        cookie: &StoredCookie,
        now: f64,
    ) -> Result<(), NotebookLMError> {
        let Some(url) = cookie_url(cookie, now)? else {
            return Ok(());
        };
        self.header_for_host(&cookie.path, cookie.domain.trim_start_matches('.'))?;
        jar.add_cookie_str(&cookie_value(cookie, now)?, &url);
        Ok(())
    }

    pub fn parse(raw: &str) -> Result<Self, NotebookLMError> {
        if raw.len() > 4 * 1024 * 1024 {
            return Err(NotebookLMError::ResponseTooLarge);
        }
        let state: Self = serde_json::from_str(raw).map_err(|_| NotebookLMError::Authentication)?;
        if state.cookies.len() > 512 {
            return Err(NotebookLMError::ResponseTooLarge);
        }
        let route = state.authuser();
        if route.len() > 320 || !route.bytes().all(|byte| byte.is_ascii_graphic()) {
            return Err(NotebookLMError::Authentication);
        }
        Ok(state)
    }

    pub fn header(&self, path: &str) -> Result<HeaderValue, NotebookLMError> {
        self.header_for_host(path, "notebooklm.google.com")
    }

    pub fn header_for_host(&self, path: &str, host: &str) -> Result<HeaderValue, NotebookLMError> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| NotebookLMError::Authentication)?
            .as_secs_f64();
        let mut values = Vec::new();
        for cookie in self
            .cookies
            .iter()
            .filter(|cookie| cookie_matches(cookie, path, host, now))
        {
            validate_cookie(cookie)?;
            values.push(format!("{}={}", cookie.name, cookie.value));
        }
        if values.is_empty() {
            return Err(NotebookLMError::Authentication);
        }
        let joined = values.join("; ");
        if joined.len() > 65_536 {
            return Err(NotebookLMError::ResponseTooLarge);
        }
        let mut header =
            HeaderValue::from_str(&joined).map_err(|_| NotebookLMError::Authentication)?;
        header.set_sensitive(true);
        Ok(header)
    }
}

fn cookie_matches(cookie: &StoredCookie, path: &str, host: &str, now: f64) -> bool {
    let domain = cookie.domain.strip_prefix('.').unwrap_or(&cookie.domain);
    (domain == "google.com" || domain == host)
        && !cookie
            .expires
            .is_some_and(|expiry| expiry >= 0.0 && expiry <= now)
        && (path == cookie.path
            || path
                .strip_prefix(&cookie.path)
                .is_some_and(|tail| cookie.path.ends_with('/') || tail.starts_with('/')))
}

fn validate_cookie(cookie: &StoredCookie) -> Result<(), NotebookLMError> {
    if cookie.name.is_empty()
        || cookie.name.len() > 128
        || cookie.value.len() > 8192
        || !cookie
            .name
            .bytes()
            .all(|byte| byte.is_ascii_graphic() && !b"()<>@,;:\\\"/[]?={}".contains(&byte))
        || !cookie
            .value
            .bytes()
            .all(|byte| byte.is_ascii_graphic() && !b"\",;\\".contains(&byte))
    {
        return Err(NotebookLMError::Authentication);
    }
    Ok(())
}

fn cookie_url(cookie: &StoredCookie, now: f64) -> Result<Option<Url>, NotebookLMError> {
    let host = cookie.domain.trim_start_matches('.');
    if !matches!(
        host,
        "google.com" | "notebooklm.google.com" | "notebook.google.com" | "accounts.google.com"
    ) || cookie
        .expires
        .is_some_and(|expiry| expiry >= 0.0 && expiry <= now)
    {
        return Ok(None);
    }
    if !cookie.path.starts_with('/')
        || cookie
            .path
            .bytes()
            .any(|byte| byte <= 32 || byte == b';' || byte >= 127)
    {
        return Err(NotebookLMError::Authentication);
    }
    Url::parse(&format!("https://{host}/"))
        .map(Some)
        .map_err(|_| NotebookLMError::Authentication)
}

fn cookie_value(cookie: &StoredCookie, now: f64) -> Result<String, NotebookLMError> {
    let mut value = format!(
        "{}={}; Path={}; Secure",
        cookie.name, cookie.value, cookie.path
    );
    if cookie.domain.starts_with('.') {
        value.push_str("; Domain=");
        value.push_str(&cookie.domain);
    }
    if let Some(expiry) = cookie.expires.filter(|expiry| *expiry >= 0.0) {
        let age = Duration::try_from_secs_f64((expiry - now).ceil())
            .map_err(|_| NotebookLMError::Authentication)?;
        value.push_str("; Max-Age=");
        value.push_str(&age.as_secs().to_string());
    }
    Ok(value)
}

pub(super) fn page_token(html: &str, key: &str) -> Result<String, NotebookLMError> {
    let html = html_escape::decode_html_entities(html);
    let pattern = format!(r#""{}"\s*:\s*("(?:[^"\\]|\\.)*")"#, regex::escape(key));
    let regex = Regex::new(&pattern).map_err(|_| NotebookLMError::Schema("page token pattern"))?;
    let encoded = regex
        .captures(&html)
        .and_then(|capture| capture.get(1))
        .ok_or(NotebookLMError::Authentication)?;
    let token: String = serde_json::from_str(encoded.as_str())?;
    if token.is_empty() || token.len() > 8192 {
        return Err(NotebookLMError::Authentication);
    }
    Ok(token)
}

pub(super) fn identifier(value: &str) -> Result<&str, NotebookLMError> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(NotebookLMError::InvalidInput);
    }
    Ok(value)
}

pub(super) fn source_identifier(result: &Value) -> Result<String, NotebookLMError> {
    let mut row = result;
    for _ in 0..3 {
        if let Some(id) = row.get(0).and_then(|first| {
            first
                .as_str()
                .or_else(|| first.get(0).and_then(Value::as_str))
        }) {
            return identifier(id).map(str::to_owned);
        }
        row = row
            .get(0)
            .ok_or(NotebookLMError::Schema("source identifier"))?;
    }
    Err(NotebookLMError::Schema("source identifier"))
}

pub(super) fn decode_rpc(raw: &str, method: &str) -> Result<Value, NotebookLMError> {
    if raw.len() > MAX_RESPONSE_BYTES {
        return Err(NotebookLMError::ResponseTooLarge);
    }
    let body = raw
        .strip_prefix(")]}'\r\n")
        .or_else(|| raw.strip_prefix(")]}'\n"))
        .unwrap_or(raw);
    let mut lines = body.lines().map(str::trim).filter(|line| !line.is_empty());
    let mut result = None;
    let mut refused = false;
    while let Some(line) = lines.next() {
        let payload = if let Ok(count) = line.parse::<usize>() {
            let payload = lines
                .next()
                .ok_or(NotebookLMError::Schema("RPC byte framing"))?;
            log_frame_length(count, payload.len());
            payload
        } else {
            line
        };
        let chunk: Value = serde_json::from_str(payload)?;
        visit(&chunk, method, &mut result, &mut refused, 0)?;
    }
    if refused {
        return Err(NotebookLMError::RpcFailure);
    }
    result.ok_or(NotebookLMError::Schema("RPC method envelope"))
}

fn log_frame_length(declared: usize, actual: usize) {
    if declared != actual {
        tracing::debug!(
            declared,
            actual,
            "NotebookLM RPC frame length differs; validating JSON envelope"
        );
    }
}

fn visit(
    value: &Value,
    method: &str,
    result: &mut Option<Value>,
    refused: &mut bool,
    depth: usize,
) -> Result<(), NotebookLMError> {
    if depth > 16 {
        return Err(NotebookLMError::Schema("RPC nesting"));
    }
    let Some(items) = value.as_array() else {
        return Ok(());
    };
    if matches!(items.first().and_then(Value::as_str), Some("wrb.fr" | "er")) {
        if items.get(1).and_then(Value::as_str) != Some(method) {
            return Ok(());
        }
        if items[0] == "er" {
            *refused = true;
            return Ok(());
        }
        let payload = items.get(2).ok_or(NotebookLMError::Schema("RPC result"))?;
        if payload.is_null()
            && items
                .get(5)
                .is_some_and(|status| !status.is_null() && status != &json!([0]))
        {
            *refused = true;
            return Ok(());
        }
        if result.is_some() {
            return Err(NotebookLMError::Schema("duplicate RPC result"));
        }
        *result = Some(match payload {
            Value::String(encoded) => serde_json::from_str(encoded)?,
            Value::Null => Value::Null,
            _ => return Err(NotebookLMError::Schema("RPC encoded result")),
        });
        return Ok(());
    }
    for item in items {
        visit(item, method, result, refused, depth + 1)?;
    }
    Ok(())
}

pub(super) fn notebook(row: &Value) -> Result<Notebook, NotebookLMError> {
    let id = row
        .get(2)
        .and_then(Value::as_str)
        .ok_or(NotebookLMError::Schema("notebook identifier"))?;
    Ok(Notebook {
        id: identifier(id)?.to_owned(),
        title: row
            .get(0)
            .and_then(Value::as_str)
            .ok_or(NotebookLMError::Schema("notebook title"))?
            .to_owned(),
    })
}

pub(super) fn source_status(
    result: &Value,
    source: &str,
) -> Result<Option<ArtifactStatus>, NotebookLMError> {
    let rows = result
        .pointer("/0/1")
        .ok_or(NotebookLMError::Schema("notebook sources"))?;
    if rows.is_null() {
        return Ok(None);
    }
    let rows = rows
        .as_array()
        .ok_or(NotebookLMError::Schema("notebook sources"))?;
    for row in rows {
        if source_identifier(row)? == source {
            let status = match row.pointer("/3/1").and_then(Value::as_i64) {
                Some(1) => ArtifactStatus::Processing,
                Some(2) => ArtifactStatus::Ready,
                Some(3) => ArtifactStatus::Failed,
                _ => ArtifactStatus::Pending,
            };
            return Ok(Some(status));
        }
    }
    Ok(None)
}

pub(super) fn artifact(row: &Value) -> Result<Artifact, NotebookLMError> {
    let id = row
        .get(0)
        .and_then(Value::as_str)
        .ok_or(NotebookLMError::Schema("artifact identifier"))?;
    let status = match row.get(4).and_then(Value::as_u64) {
        Some(1) => ArtifactStatus::Processing,
        Some(2) => ArtifactStatus::Pending,
        Some(3) => ArtifactStatus::Ready,
        Some(4) => ArtifactStatus::Failed,
        _ => return Err(NotebookLMError::Schema("artifact status")),
    };
    Ok(Artifact {
        id: identifier(id)?.to_owned(),
        status,
    })
}

pub(super) fn client_options(artifacts: bool) -> Value {
    let mut options = vec![
        json!(2),
        Value::Null,
        Value::Null,
        json!([1, null, null, null, null, null, null, null, null, null, [1]]),
    ];
    if artifacts {
        options.push(json!([[1, 4, 8, 2, 3, 6]]));
    }
    Value::Array(options)
}

pub(super) fn flashcard_params(
    notebook: &str,
    sources: &[String],
    prompt: &str,
    options: &GenerationOptions,
) -> Value {
    let sources = sources
        .iter()
        .map(|source| json!([[source]]))
        .collect::<Vec<_>>();
    let quantity = match options.quantity() {
        Quantity::Fewer => 1,
        Quantity::Standard => 2,
        Quantity::More => 3,
    };
    let difficulty = match options.difficulty() {
        Difficulty::Easy => 1,
        Difficulty::Medium => 2,
        Difficulty::Hard => 3,
    };
    json!([
        client_options(true),
        notebook,
        [
            null,
            null,
            4,
            sources,
            null,
            null,
            null,
            null,
            null,
            [
                null,
                [1, null, prompt, null, null, null, [quantity, difficulty]]
            ]
        ]
    ])
}

pub(super) fn flashcards(html: &str) -> Result<Vec<Flashcard>, NotebookLMError> {
    if html.len() > MAX_RESPONSE_BYTES {
        return Err(NotebookLMError::ResponseTooLarge);
    }
    let regex = lazy_regex::regex!(r#"data-app-data\s*=\s*"([^"]+)""#);
    let encoded = regex
        .captures(html)
        .and_then(|capture| capture.get(1))
        .ok_or(NotebookLMError::Schema("flashcard HTML attribute"))?;
    let data: Value = serde_json::from_str(&html_escape::decode_html_entities(encoded.as_str()))?;
    let cards = if data.is_array() {
        &data
    } else {
        data.get("cards")
            .or_else(|| data.get("flashcards"))
            .ok_or(NotebookLMError::Schema("flashcard array"))?
    };
    let cards = cards
        .as_array()
        .ok_or(NotebookLMError::Schema("flashcard array"))?;
    if cards.len() > 10_000 {
        return Err(NotebookLMError::ResponseTooLarge);
    }
    cards
        .iter()
        .map(|card| {
            let front = card_text(card, &["front", "question", "q", "f"])?;
            let back = card_text(card, &["back", "answer", "a", "b"])?;
            Ok(Flashcard {
                front,
                back,
                ..Flashcard::default()
            })
        })
        .collect()
}

fn card_text(card: &Value, names: &[&str]) -> Result<String, NotebookLMError> {
    let value = names
        .iter()
        .find_map(|name| card.get(name))
        .ok_or(NotebookLMError::Schema("flashcard text"))?;
    let text = if let Some(text) = value.as_str() {
        text.to_owned()
    } else {
        let blocks = value
            .get("flashcardContentBlock")
            .and_then(Value::as_array)
            .ok_or(NotebookLMError::Schema("flashcard content blocks"))?;
        if blocks.len() > 128 {
            return Err(NotebookLMError::ResponseTooLarge);
        }
        let mut contents = Vec::with_capacity(blocks.len());
        for block in blocks {
            contents.push(block_text(block)?);
        }
        contents.join("\n")
    };
    if text.trim().is_empty() {
        return Err(NotebookLMError::Schema("flashcard text"));
    }
    Ok(text)
}

fn block_text(block: &Value) -> Result<&str, NotebookLMError> {
    let kind = block
        .get("type")
        .and_then(Value::as_str)
        .ok_or(NotebookLMError::Schema("flashcard block type"))?;
    if !kind.eq_ignore_ascii_case("text") && !kind.eq_ignore_ascii_case("markdown") {
        return Err(NotebookLMError::Schema("unsupported flashcard block type"));
    }
    block
        .get("content")
        .and_then(Value::as_str)
        .ok_or(NotebookLMError::Schema("flashcard block content"))
}
