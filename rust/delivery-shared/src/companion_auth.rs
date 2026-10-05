use axum::http::{HeaderMap, header};
use flashcards_domain::identity::UserId;
use flashcards_integrations_shared::companion_auth::{CompanionAuthError, RemoteTokenVerifier};
use flashcards_services::authentication::CompanionTokenVerifier;
use std::{error::Error, fmt};

#[derive(Debug)]
pub enum AuthorizationError {
    Origin,
    Credentials,
    Expired,
    Unavailable(CompanionAuthError),
}

impl fmt::Display for AuthorizationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Origin => formatter.write_str("Origem não permitida"),
            Self::Credentials => formatter.write_str("Sessão do aplicativo necessária"),
            Self::Expired => formatter.write_str("Sessão do aplicativo expirada"),
            Self::Unavailable(error) => fmt::Display::fmt(error, formatter),
        }
    }
}

impl Error for AuthorizationError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Unavailable(error) => Some(error),
            _ => None,
        }
    }
}

/// # Errors
/// Rejects missing, repeated, or invalid local authorization headers and propagates remote verification failures.
pub async fn authorized_user(
    headers: &HeaderMap,
    verifier: &RemoteTokenVerifier,
) -> Result<UserId, AuthorizationError> {
    if single_header(headers, header::ORIGIN) != Some(verifier.origin()) {
        return Err(AuthorizationError::Origin);
    }
    let token = single_header(headers, header::AUTHORIZATION)
        .and_then(bearer_token)
        .ok_or(AuthorizationError::Credentials)?;
    verifier
        .verify(token)
        .await
        .map_err(|error| match error {
            CompanionAuthError::Identity(_) => AuthorizationError::Expired,
            error => AuthorizationError::Unavailable(error),
        })?
        .ok_or(AuthorizationError::Expired)
}

fn single_header(headers: &HeaderMap, name: header::HeaderName) -> Option<&str> {
    let mut values = headers.get_all(name).iter();
    let value = values.next()?.to_str().ok()?;
    values.next().is_none().then_some(value)
}

fn bearer_token(value: &str) -> Option<&str> {
    let (scheme, token) = value.split_once(' ')?;
    (scheme.eq_ignore_ascii_case("Bearer")
        && !token.is_empty()
        && !token.bytes().any(|byte| byte.is_ascii_whitespace()))
    .then_some(token)
}

#[cfg(test)]
mod tests;
