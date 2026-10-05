use crate::http_body::{self, BodyReadError};
use flashcards_domain::identity::{InvalidUserId, UserId};
use flashcards_services::authentication::CompanionTokenVerifier;
use reqwest::{Client, StatusCode, Url, redirect::Policy};
use serde::{Deserialize, Serialize};
use std::{error::Error, fmt, time::Duration};

const MAX_RESPONSE_BYTES: usize = 8_192;

#[derive(Debug)]
pub enum CompanionAuthError {
    InvalidOrigin,
    Http(reqwest::Error),
    UnexpectedStatus(StatusCode),
    OversizedResponse,
    Json(serde_json::Error),
    Identity(InvalidUserId),
}

impl fmt::Display for CompanionAuthError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidOrigin => "web_origin must be an exact HTTPS or local HTTP origin",
            Self::Http(_) | Self::UnexpectedStatus(_) => {
                "Não foi possível validar a sessão do aplicativo."
            }
            Self::OversizedResponse | Self::Json(_) | Self::Identity(_) => {
                "A validação da sessão retornou uma resposta inválida."
            }
        })
    }
}

impl Error for CompanionAuthError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Http(error) => Some(error),
            Self::Json(error) => Some(error),
            Self::Identity(error) => Some(error),
            _ => None,
        }
    }
}

#[derive(Clone)]
pub struct RemoteTokenVerifier {
    client: Client,
    origin: String,
    endpoint: Url,
}

impl RemoteTokenVerifier {
    /// # Errors
    /// Rejects unsafe origins and propagates HTTP client setup failures.
    pub fn new(web_origin: &str) -> Result<Self, CompanionAuthError> {
        let origin = normalized_origin(web_origin)?;
        let endpoint = Url::parse(&format!("{origin}/api/v1/auth/companion/verify"))
            .map_err(|_| CompanionAuthError::InvalidOrigin)?;
        let client = Client::builder()
            .timeout(Duration::from_secs(5))
            .redirect(Policy::none())
            .retry(reqwest::retry::never())
            .build()
            .map_err(CompanionAuthError::Http)?;
        Ok(Self {
            client,
            origin,
            endpoint,
        })
    }

    #[must_use]
    pub fn origin(&self) -> &str {
        &self.origin
    }
}

fn normalized_origin(value: &str) -> Result<String, CompanionAuthError> {
    let (_, authority) = value
        .trim()
        .split_once("://")
        .ok_or(CompanionAuthError::InvalidOrigin)?;
    if authority
        .strip_suffix('/')
        .unwrap_or(authority)
        .contains(['/', '\\', '@', '?', '#'])
    {
        return Err(CompanionAuthError::InvalidOrigin);
    }
    let url = Url::parse(value.trim()).map_err(|_| CompanionAuthError::InvalidOrigin)?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || (url.scheme() == "http"
            && !matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]")))
    {
        return Err(CompanionAuthError::InvalidOrigin);
    }
    Ok(url.origin().ascii_serialization())
}

#[cfg(test)]
mod tests;

#[derive(Serialize)]
struct TokenRequest<'a> {
    access_token: &'a str,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RemoteIdentity {
    subject: String,
}

impl CompanionTokenVerifier for RemoteTokenVerifier {
    type Error = CompanionAuthError;

    async fn verify(&self, token: &str) -> Result<Option<UserId>, Self::Error> {
        if token.is_empty() || token.len() > 4096 {
            return Ok(None);
        }
        let response = self
            .client
            .post(self.endpoint.clone())
            .json(&TokenRequest {
                access_token: token,
            })
            .send()
            .await
            .map_err(CompanionAuthError::Http)?;
        match response.status() {
            StatusCode::UNAUTHORIZED => return Ok(None),
            StatusCode::CREATED => {}
            status => return Err(CompanionAuthError::UnexpectedStatus(status)),
        }
        let bytes = http_body::read_bounded(response, MAX_RESPONSE_BYTES)
            .await
            .map_err(|error| match error {
                BodyReadError::Http(error) => CompanionAuthError::Http(error),
                BodyReadError::TooLarge => CompanionAuthError::OversizedResponse,
            })?;
        let identity: RemoteIdentity =
            serde_json::from_slice(&bytes).map_err(CompanionAuthError::Json)?;
        UserId::try_from(identity.subject)
            .map(Some)
            .map_err(CompanionAuthError::Identity)
    }
}
