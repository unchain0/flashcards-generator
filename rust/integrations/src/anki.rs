use crate::http_body::{self, BodyReadError};
use flashcards_domain::Deck;
use flashcards_engines::math::convert_to_anki_math_format;
use flashcards_services::anki::AnkiGateway;
use reqwest::{Client, StatusCode, Url, redirect::Policy};
use serde_json::{Value, json};
use std::{error::Error, fmt, net::IpAddr, time::Duration};

pub const DEFAULT_ANKI_CONNECT_URL: &str = "http://127.0.0.1:8765";
const MAX_REQUEST_BYTES: usize = 8 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 1024 * 1024;

#[derive(Debug)]
pub enum AnkiError {
    InvalidInput,
    Http(reqwest::Error),
    Status(StatusCode),
    Json(serde_json::Error),
    TooLarge,
    Protocol,
    Rejected,
    PartialImport { note_ids: Vec<Option<u64>> },
}
impl fmt::Display for AnkiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidInput => "Invalid local AnkiConnect input",
            Self::Http(_) | Self::Status(_) => {
                "Local AnkiConnect request failed; do not repeat unconfirmed imports"
            }
            Self::Json(_) | Self::Protocol => "Invalid AnkiConnect response",
            Self::TooLarge => "AnkiConnect request or response exceeds size limit",
            Self::Rejected => "AnkiConnect refused the operation",
            Self::PartialImport { .. } => "AnkiConnect imported only part of the deck",
        })
    }
}
impl Error for AnkiError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Http(error) => Some(error),
            Self::Json(error) => Some(error),
            _ => None,
        }
    }
}

pub struct AnkiConnectClient {
    client: Client,
    endpoint: Url,
    api_key: Option<String>,
}
impl AnkiConnectClient {
    /// # Errors
    /// Rejects nonlocal or unsafe endpoints and invalid API keys; propagates HTTP client setup failures.
    pub fn new(endpoint: &str, api_key: Option<String>) -> Result<Self, AnkiError> {
        let mut endpoint = Url::parse(endpoint).map_err(|_| AnkiError::InvalidInput)?;
        if endpoint.host_str() == Some("localhost") {
            endpoint
                .set_host(Some("127.0.0.1"))
                .map_err(|_| AnkiError::InvalidInput)?;
        }
        if endpoint.scheme() != "http"
            || !endpoint.host_str().is_some_and(|host| {
                host.trim_matches(['[', ']'])
                    .parse::<IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback())
            })
            || !endpoint.username().is_empty()
            || endpoint.password().is_some()
            || endpoint.path() != "/"
            || endpoint.query().is_some()
            || endpoint.fragment().is_some()
            || api_key
                .as_ref()
                .is_some_and(|key| key.is_empty() || key.len() > 4096)
        {
            return Err(AnkiError::InvalidInput);
        }
        let client = Client::builder()
            .timeout(Duration::from_secs(10))
            .no_proxy()
            .redirect(Policy::none())
            .retry(reqwest::retry::never())
            .build()
            .map_err(AnkiError::Http)?;
        Ok(Self {
            client,
            endpoint,
            api_key,
        })
    }

    async fn invoke(&self, action: &str, params: Value) -> Result<Value, AnkiError> {
        let mut request = json!({"action": action, "version": 6, "params": params});
        if let Some(key) = &self.api_key {
            request["key"] = json!(key);
        }
        let body = serde_json::to_vec(&request).map_err(AnkiError::Json)?;
        if body.len() > MAX_REQUEST_BYTES {
            return Err(AnkiError::TooLarge);
        }
        let response = self
            .client
            .post(self.endpoint.clone())
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
            .send()
            .await
            .map_err(AnkiError::Http)?;
        if !response.status().is_success() {
            return Err(AnkiError::Status(response.status()));
        }
        let bytes = http_body::read_bounded(response, MAX_RESPONSE_BYTES)
            .await
            .map_err(|error| match error {
                BodyReadError::Http(error) => AnkiError::Http(error),
                BodyReadError::TooLarge => AnkiError::TooLarge,
            })?;
        result_envelope(&bytes)
    }
}

fn result_envelope(bytes: &[u8]) -> Result<Value, AnkiError> {
    let mut envelope: Value = serde_json::from_slice(bytes).map_err(AnkiError::Json)?;
    if !envelope.is_object() || envelope.get("result").is_none() || envelope.get("error").is_none()
    {
        return Err(AnkiError::Protocol);
    }
    if !envelope["error"].is_null() {
        return Err(if envelope["error"].is_string() {
            AnkiError::Rejected
        } else {
            AnkiError::Protocol
        });
    }
    Ok(envelope["result"].take())
}

fn valid_name(value: &str) -> bool {
    !value.trim().is_empty()
        && value.chars().count() <= 1000
        && !value.chars().any(char::is_control)
}
fn strings(value: Value) -> Result<Vec<String>, AnkiError> {
    let result: Vec<String> = serde_json::from_value(value).map_err(|_| AnkiError::Protocol)?;
    if result.iter().any(|name| !valid_name(name)) {
        return Err(AnkiError::Protocol);
    }
    Ok(result)
}
fn note_ids(value: Value) -> Result<Vec<u64>, AnkiError> {
    let result: Vec<u64> = serde_json::from_value(value).map_err(|_| AnkiError::Protocol)?;
    if result.contains(&0) {
        return Err(AnkiError::Protocol);
    }
    Ok(result)
}

impl AnkiGateway for AnkiConnectClient {
    type Error = AnkiError;
    async fn version(&self) -> Result<u64, AnkiError> {
        self.invoke("version", json!({}))
            .await?
            .as_u64()
            .filter(|version| *version >= 6)
            .ok_or(AnkiError::Protocol)
    }
    async fn deck_names(&self) -> Result<Vec<String>, AnkiError> {
        strings(self.invoke("deckNames", json!({})).await?)
    }
    async fn find_notes(&self, query: &str) -> Result<Vec<u64>, AnkiError> {
        if query.len() > 16_384 || query.contains('\0') {
            return Err(AnkiError::InvalidInput);
        }
        note_ids(self.invoke("findNotes", json!({"query": query})).await?)
    }
    async fn model_field_names(&self, model: &str) -> Result<Vec<String>, AnkiError> {
        if !valid_name(model) {
            return Err(AnkiError::InvalidInput);
        }
        strings(
            self.invoke("modelFieldNames", json!({"modelName": model}))
                .await?,
        )
    }
    async fn export_cloze(&self, deck: &Deck, destination: &str) -> Result<Vec<u64>, AnkiError> {
        if !valid_name(destination) || deck.total_cards() > 10_000 {
            return Err(AnkiError::InvalidInput);
        }
        let notes = deck.flashcards.iter().map(|card| json!({
            "deckName": destination, "modelName": "Cloze",
            "fields": {"Text": convert_to_anki_math_format(&card.front), "Extra": convert_to_anki_math_format(&card.back)},
            "tags": card.tags,
            "options": {"allowDuplicate": false, "duplicateScope": "deck", "duplicateScopeOptions": {"deckName": destination, "checkChildren": false, "checkAllModels": false}}
        })).collect::<Vec<_>>();
        let params = json!({"notes": notes});
        if serde_json::to_vec(&params).map_err(AnkiError::Json)?.len() > MAX_REQUEST_BYTES - 8192 {
            return Err(AnkiError::TooLarge);
        }
        self.invoke("createDeck", json!({"deck": destination}))
            .await?
            .as_u64()
            .filter(|id| *id > 0)
            .ok_or(AnkiError::Protocol)?;
        if deck.flashcards.is_empty() {
            return Ok(Vec::new());
        }
        let ids: Vec<Option<u64>> = serde_json::from_value(self.invoke("addNotes", params).await?)
            .map_err(|_| AnkiError::Protocol)?;
        if ids.len() != deck.total_cards() || ids.contains(&Some(0)) {
            return Err(AnkiError::Protocol);
        }
        if ids.contains(&None) {
            return Err(AnkiError::PartialImport { note_ids: ids });
        }
        Ok(ids.into_iter().flatten().collect())
    }
}

#[cfg(test)]
mod tests;
