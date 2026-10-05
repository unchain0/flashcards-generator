use argon2::{
    Algorithm, Argon2, Params, PasswordHash, PasswordHasher, PasswordVerifier, Version,
    password_hash::{self, SaltString},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::{error::Error, fmt};

pub const COMPANION_TOKEN_TTL_SECONDS: u64 = 300;
const AUDIENCE: &str = "flashcards-companion";
const MAX_PASSWORD_LENGTH: usize = 256;

#[derive(Debug)]
pub enum AuthCryptoError {
    InvalidSecret,
    InvalidPassword,
    Parameters(argon2::Error),
    Entropy(getrandom::Error),
    Hash(password_hash::Error),
    Json(serde_json::Error),
}

impl fmt::Display for AuthCryptoError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidSecret => {
                formatter.write_str("Authentication secrets require 32 characters")
            }
            Self::InvalidPassword => {
                formatter.write_str("A senha deve ter entre 12 e 256 caracteres")
            }
            Self::Entropy(_) => formatter.write_str("Secure randomness unavailable"),
            Self::Parameters(_) => formatter.write_str("Password hashing configuration failed"),
            Self::Hash(_) => formatter.write_str("Password hashing failed"),
            Self::Json(_) => formatter.write_str("Companion token encoding failed"),
        }
    }
}

impl Error for AuthCryptoError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Entropy(error) => Some(error),
            Self::Parameters(error) => Some(error),
            Self::Hash(error) => Some(error),
            Self::Json(error) => Some(error),
            Self::InvalidSecret | Self::InvalidPassword => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompanionClaims {
    pub audience: String,
    pub expires_at: u64,
    pub issued_at: u64,
    pub nonce: String,
    pub session_hash: String,
    pub subject: String,
}

pub struct AuthCrypto {
    session_mac: Hmac<Sha256>,
    lookup_mac: Hmac<Sha256>,
    hasher: Argon2<'static>,
}

impl AuthCrypto {
    /// # Errors
    /// Rejects short secrets and propagates hashing parameter or MAC initialization failures.
    pub fn new(session_secret: &str, lookup_secret: &str) -> Result<Self, AuthCryptoError> {
        if session_secret.chars().count() < 32 || lookup_secret.chars().count() < 32 {
            return Err(AuthCryptoError::InvalidSecret);
        }
        let params = Params::new(65_536, 3, 4, Some(32)).map_err(AuthCryptoError::Parameters)?;
        Ok(Self {
            session_mac: Hmac::<Sha256>::new_from_slice(session_secret.as_bytes())
                .map_err(|_| AuthCryptoError::InvalidSecret)?,
            lookup_mac: Hmac::<Sha256>::new_from_slice(lookup_secret.as_bytes())
                .map_err(|_| AuthCryptoError::InvalidSecret)?,
            hasher: Argon2::new(Algorithm::Argon2id, Version::V0x13, params),
        })
    }

    /// # Errors
    /// Rejects passwords outside the supported character limits.
    pub fn validate_password(password: &str) -> Result<(), AuthCryptoError> {
        if !(12..=MAX_PASSWORD_LENGTH).contains(&password.chars().count()) {
            return Err(AuthCryptoError::InvalidPassword);
        }
        Ok(())
    }

    #[must_use]
    pub fn password_lookup(&self, password: &str) -> String {
        keyed_hex(&self.lookup_mac, password.as_bytes())
    }

    #[must_use]
    pub fn session_hash(&self, token: &str) -> String {
        keyed_hex(&self.session_mac, token.as_bytes())
    }

    /// # Errors
    /// Rejects invalid passwords and propagates entropy, salt encoding, and hashing failures.
    pub fn hash_password(&self, password: &str) -> Result<String, AuthCryptoError> {
        Self::validate_password(password)?;
        self.hash_with_fresh_salt(password)
    }

    fn hash_with_fresh_salt(&self, password: &str) -> Result<String, AuthCryptoError> {
        let mut salt = [0; 16];
        getrandom::fill(&mut salt).map_err(AuthCryptoError::Entropy)?;
        let salt = SaltString::encode_b64(&salt).map_err(AuthCryptoError::Hash)?;
        self.hasher
            .hash_password(password.as_bytes(), &salt)
            .map(|hash| hash.to_string())
            .map_err(AuthCryptoError::Hash)
    }

    #[must_use]
    pub fn verify_password(&self, hash: &str, password: &str) -> bool {
        if password.is_empty() || password.chars().count() > MAX_PASSWORD_LENGTH {
            return false;
        }
        PasswordHash::new(hash).is_ok_and(|hash| {
            self.hasher
                .verify_password(password.as_bytes(), &hash)
                .is_ok()
        })
    }

    /// # Errors
    /// Propagates entropy failures.
    pub fn new_session_token(&self) -> Result<String, AuthCryptoError> {
        random_token::<32>()
    }

    #[must_use]
    pub fn password_needs_rehash(&self, hash: &str) -> bool {
        let Ok(hash) = PasswordHash::new(hash) else {
            return true;
        };
        hash.algorithm.as_str() != "argon2id"
            || hash.version != Some(19)
            || Params::try_from(&hash).as_ref() != Ok(self.hasher.params())
            || hash.salt.is_none_or(|salt| salt.len() != 22)
    }

    pub(crate) fn verify_and_rehash_password(
        &self,
        hash: &str,
        password: &str,
    ) -> Result<(bool, Option<String>), AuthCryptoError> {
        if !self.verify_password(hash, password) {
            return Ok((false, None));
        }
        let replacement = if self.password_needs_rehash(hash) {
            Some(self.hash_with_fresh_salt(password)?)
        } else {
            None
        };
        Ok((true, replacement))
    }

    /// # Errors
    /// Propagates entropy and claim serialization failures.
    pub fn create_companion_token(
        &self,
        subject: &str,
        session_token: &str,
        now: u64,
    ) -> Result<String, AuthCryptoError> {
        let claims = CompanionClaims {
            audience: AUDIENCE.into(),
            expires_at: now.saturating_add(COMPANION_TOKEN_TTL_SECONDS),
            issued_at: now,
            nonce: random_token::<16>()?,
            session_hash: self.session_hash(session_token),
            subject: subject.into(),
        };
        let json = serde_json::to_vec(&claims).map_err(AuthCryptoError::Json)?;
        let payload = URL_SAFE_NO_PAD.encode(json);
        let signature = self.companion_mac(&payload).finalize().into_bytes();
        Ok(format!("{payload}.{}", URL_SAFE_NO_PAD.encode(signature)))
    }

    #[must_use]
    pub fn verified_companion_claims(&self, token: &str, now: u64) -> Option<CompanionClaims> {
        if token.len() > 4096 {
            return None;
        }
        let (payload, signature) = token.split_once('.')?;
        let signature = URL_SAFE_NO_PAD.decode(signature).ok()?;
        self.companion_mac(payload).verify_slice(&signature).ok()?;
        let decoded = URL_SAFE_NO_PAD.decode(payload).ok()?;
        let claims: CompanionClaims = serde_json::from_slice(&decoded).ok()?;
        claims.current(now).then_some(claims)
    }

    fn companion_mac(&self, payload: &str) -> Hmac<Sha256> {
        let mut mac = self.session_mac.clone();
        mac.update(b"flashcards-companion\0");
        mac.update(payload.as_bytes());
        mac
    }
}

impl CompanionClaims {
    fn current(&self, now: u64) -> bool {
        self.audience == AUDIENCE
            && self.issued_at <= now
            && now < self.expires_at
            && self.expires_at <= self.issued_at.saturating_add(COMPANION_TOKEN_TTL_SECONDS)
            && self.session_hash.len() == 64
            && self.subject.len() == 32
    }
}

fn random_token<const N: usize>() -> Result<String, AuthCryptoError> {
    let mut bytes = [0; N];
    getrandom::fill(&mut bytes).map_err(AuthCryptoError::Entropy)?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

fn keyed_hex(template: &Hmac<Sha256>, value: &[u8]) -> String {
    let mut mac = template.clone();
    mac.update(value);
    crate::hex_encoding::lowercase_hex(&mac.finalize().into_bytes())
}

#[cfg(test)]
mod tests;
