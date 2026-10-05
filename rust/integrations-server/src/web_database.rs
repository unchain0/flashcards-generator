use crate::auth_crypto::{AuthCrypto, AuthCryptoError};
use flashcards_services::authentication::WebAuthStore;
use sqlx::{
    PgPool,
    postgres::{PgConnectOptions, PgPoolOptions},
};
use std::{
    error::Error,
    fmt,
    str::FromStr,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    sync::{AcquireError, Semaphore},
    task::JoinError,
};

#[derive(Debug)]
pub enum DatabaseError {
    Sql(sqlx::Error),
    Crypto(AuthCryptoError),
    Migration(sqlx::migrate::MigrateError),
    HashWorker(JoinError),
    HashCapacity(AcquireError),
    PasswordAlreadyProvisioned,
    InvalidClock,
}

impl fmt::Display for DatabaseError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Sql(_) => "Database operation failed",
            Self::Crypto(_) => "Authentication cryptography failed",
            Self::Migration(_) => "Database migration failed",
            Self::HashWorker(_) => "Password worker failed",
            Self::HashCapacity(_) => "Password worker capacity unavailable",
            Self::PasswordAlreadyProvisioned => "Essa senha já está provisionada",
            Self::InvalidClock => "System clock precedes Unix epoch",
        })
    }
}

impl Error for DatabaseError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Sql(error) => Some(error),
            Self::Crypto(error) => Some(error),
            Self::Migration(error) => Some(error),
            Self::HashWorker(error) => Some(error),
            Self::HashCapacity(error) => Some(error),
            Self::PasswordAlreadyProvisioned | Self::InvalidClock => None,
        }
    }
}

impl From<sqlx::Error> for DatabaseError {
    fn from(error: sqlx::Error) -> Self {
        Self::Sql(error)
    }
}

impl From<AuthCryptoError> for DatabaseError {
    fn from(error: AuthCryptoError) -> Self {
        Self::Crypto(error)
    }
}

#[derive(Clone)]
pub struct WebDatabase {
    pool: PgPool,
    crypto: Arc<AuthCrypto>,
    hash_limiter: Arc<Semaphore>,
}

impl WebDatabase {
    /// # Errors
    /// Propagates database configuration and connection failures.
    pub async fn connect(database_url: &str, crypto: AuthCrypto) -> Result<Self, DatabaseError> {
        let normalized = database_url.replacen("postgresql+asyncpg://", "postgresql://", 1);
        let options = PgConnectOptions::from_str(&normalized)?
            .options([("statement_timeout", "10000"), ("lock_timeout", "5000")]);
        let pool = PgPoolOptions::new()
            .max_connections(15)
            .acquire_timeout(Duration::from_secs(10))
            .connect_with(options)
            .await?;
        Ok(Self {
            pool,
            crypto: Arc::new(crypto),
            hash_limiter: Arc::new(Semaphore::new(2)),
        })
    }

    /// # Errors
    /// Propagates schema migration failures.
    pub async fn migrate(&self) -> Result<(), DatabaseError> {
        sqlx::migrate!("./migrations")
            .run(&self.pool)
            .await
            .map_err(DatabaseError::Migration)
    }

    /// # Errors
    /// Propagates required table and column validation failures.
    pub async fn check(&self) -> Result<(), DatabaseError> {
        sqlx::query("SELECT id, password_lookup, password_hash FROM web_users LIMIT 0")
            .execute(&self.pool)
            .await?;
        sqlx::query("SELECT token_hash, user_id, expires_at FROM web_sessions LIMIT 0")
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    /// # Errors
    /// Propagates password hashing, entropy, and persistence failures; rejects already provisioned passwords.
    pub async fn create_user(&self, password: &str) -> Result<String, DatabaseError> {
        let hash = self.hash_password(password.to_owned()).await?;
        let lookup = self.crypto.password_lookup(password);
        let id = random_user_id()?;
        let inserted = sqlx::query(
            "INSERT INTO web_users (id, password_lookup, password_hash) VALUES ($1, $2, $3) \
             ON CONFLICT (password_lookup) DO NOTHING",
        )
        .bind(&id)
        .bind(lookup)
        .bind(hash)
        .execute(&self.pool)
        .await?;
        if inserted.rows_affected() == 0 {
            return Err(DatabaseError::PasswordAlreadyProvisioned);
        }
        Ok(id)
    }

    /// # Errors
    /// Rejects invalid passwords and propagates lookup, hashing, and provisioning failures.
    pub async fn ensure_bootstrap(&self, password: Option<&str>) -> Result<(), DatabaseError> {
        let Some(password) = password.filter(|password| !password.is_empty()) else {
            return Ok(());
        };
        AuthCrypto::validate_password(password)?;
        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM web_users WHERE password_lookup = $1)")
                .bind(self.crypto.password_lookup(password))
                .fetch_one(&self.pool)
                .await?;
        if exists {
            return Ok(());
        }
        match self.create_user(password).await {
            Ok(_) | Err(DatabaseError::PasswordAlreadyProvisioned) => {}
            Err(error) => return Err(error),
        }
        Ok(())
    }

    pub async fn close(&self) {
        self.pool.close().await;
    }

    async fn hash_password(&self, password: String) -> Result<String, DatabaseError> {
        let crypto = Arc::clone(&self.crypto);
        let permit = Arc::clone(&self.hash_limiter)
            .acquire_owned()
            .await
            .map_err(DatabaseError::HashCapacity)?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            crypto.hash_password(&password)
        })
        .await
        .map_err(DatabaseError::HashWorker)?
        .map_err(DatabaseError::Crypto)
    }

    async fn verify_password(
        &self,
        hash: String,
        password: String,
    ) -> Result<(bool, Option<String>), DatabaseError> {
        let crypto = Arc::clone(&self.crypto);
        let permit = Arc::clone(&self.hash_limiter)
            .acquire_owned()
            .await
            .map_err(DatabaseError::HashCapacity)?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            crypto.verify_and_rehash_password(&hash, &password)
        })
        .await
        .map_err(DatabaseError::HashWorker)?
        .map_err(DatabaseError::Crypto)
    }
}

impl WebAuthStore for WebDatabase {
    type Error = DatabaseError;

    async fn authenticate_and_create_session(
        &self,
        password: &str,
        ttl_seconds: u32,
    ) -> Result<Option<String>, Self::Error> {
        if password.is_empty() || password.chars().count() > 256 {
            return Ok(None);
        }
        let record: Option<(String, String)> =
            sqlx::query_as("SELECT id, password_hash FROM web_users WHERE password_lookup = $1")
                .bind(self.crypto.password_lookup(password))
                .fetch_optional(&self.pool)
                .await?;
        let Some((user_id, hash)) = record else {
            return Ok(None);
        };
        let (verified, replacement) = self
            .verify_password(hash.clone(), password.to_owned())
            .await?;
        if !verified {
            return Ok(None);
        }
        let token = self.crypto.new_session_token()?;
        let mut transaction = self.pool.begin().await?;
        if let Some(replacement) = replacement {
            sqlx::query(
                "UPDATE web_users SET password_hash = $1 WHERE id = $2 AND password_hash = $3",
            )
            .bind(replacement)
            .bind(&user_id)
            .bind(hash)
            .execute(&mut *transaction)
            .await?;
        }
        sqlx::query(
            "INSERT INTO web_sessions (token_hash, user_id, expires_at) \
                     VALUES ($1, $2, clock_timestamp() + $3 * INTERVAL '1 second')",
        )
        .bind(self.crypto.session_hash(&token))
        .bind(user_id)
        .bind(f64::from(ttl_seconds))
        .execute(&mut *transaction)
        .await?;
        sqlx::query("DELETE FROM web_sessions WHERE expires_at <= clock_timestamp()")
            .execute(&mut *transaction)
            .await?;
        transaction.commit().await?;
        Ok(Some(token))
    }

    async fn delete_session(&self, token: Option<&str>) -> Result<(), Self::Error> {
        if let Some(token) = token.filter(|token| !token.is_empty()) {
            sqlx::query("DELETE FROM web_sessions WHERE token_hash = $1")
                .bind(self.crypto.session_hash(token))
                .execute(&self.pool)
                .await?;
        }
        Ok(())
    }

    async fn user_for_session(&self, token: Option<&str>) -> Result<Option<String>, Self::Error> {
        let Some(token) = token.filter(|token| !token.is_empty()) else {
            return Ok(None);
        };
        sqlx::query_scalar("SELECT user_id FROM web_sessions WHERE token_hash = $1 AND expires_at > clock_timestamp()")
            .bind(self.crypto.session_hash(token)).fetch_optional(&self.pool).await.map_err(DatabaseError::Sql)
    }

    async fn create_companion_token(
        &self,
        token: Option<&str>,
    ) -> Result<Option<String>, Self::Error> {
        let Some(token) = token else {
            return Ok(None);
        };
        let Some(user_id) = self.user_for_session(Some(token)).await? else {
            return Ok(None);
        };
        self.crypto
            .create_companion_token(&user_id, token, unix_seconds(SystemTime::now())?)
            .map(Some)
            .map_err(DatabaseError::Crypto)
    }

    async fn user_for_companion_token(&self, token: &str) -> Result<Option<String>, Self::Error> {
        let Some(claims) = self
            .crypto
            .verified_companion_claims(token, unix_seconds(SystemTime::now())?)
        else {
            return Ok(None);
        };
        sqlx::query_scalar("SELECT user_id FROM web_sessions \
                            WHERE token_hash = $1 AND user_id = $2 AND expires_at > clock_timestamp()")
            .bind(claims.session_hash).bind(claims.subject).fetch_optional(&self.pool).await.map_err(DatabaseError::Sql)
    }
}

fn unix_seconds(timestamp: SystemTime) -> Result<u64, DatabaseError> {
    timestamp
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|_| DatabaseError::InvalidClock)
}

fn random_user_id() -> Result<String, AuthCryptoError> {
    let mut bytes = [0; 16];
    getrandom::fill(&mut bytes).map_err(AuthCryptoError::Entropy)?;
    Ok(crate::hex_encoding::lowercase_hex(&bytes))
}

#[cfg(test)]
mod tests;
