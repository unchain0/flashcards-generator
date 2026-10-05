use axum::http::{HeaderValue, Uri};
use std::{collections::BTreeMap, env::VarError, error::Error, fmt, net::IpAddr, path::PathBuf};

pub struct WebConfig {
    pub production: bool,
    pub host: IpAddr,
    pub port: u16,
    pub database_url: String,
    pub session_secret: String,
    pub lookup_secret: String,
    pub bootstrap_password: Option<String>,
    pub session_ttl_seconds: u32,
    pub static_dir: PathBuf,
    pub cors_origins: Vec<HeaderValue>,
}

#[derive(Debug)]
pub struct ConfigError(&'static str);

impl fmt::Display for ConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl Error for ConfigError {}

impl WebConfig {
    /// # Errors
    /// Rejects invalid environment values and missing production secrets.
    pub fn from_env() -> Result<Self, ConfigError> {
        let mut values = BTreeMap::new();
        for name in [
            "FLASHCARDS_ENVIRONMENT",
            "FLASHCARDS_SESSION_SECRET",
            "FLASHCARDS_AUTH_LOOKUP_SECRET",
            "FLASHCARDS_AUTO_CREATE_SCHEMA",
            "FLASHCARDS_DATABASE_URL",
            "FLASHCARDS_HOST",
            "FLASHCARDS_PORT",
            "FLASHCARDS_SESSION_TTL_SECONDS",
            "FLASHCARDS_CORS_ORIGINS",
            "FLASHCARDS_BOOTSTRAP_PASSWORD",
            "FLASHCARDS_STATIC_DIR",
        ] {
            let value = match std::env::var(name) {
                Ok(value) => value,
                Err(VarError::NotPresent) => continue,
                Err(VarError::NotUnicode(_)) => return Err(ConfigError(name)),
            };
            values.insert(name.into(), value);
        }
        Self::from_values(&values)
    }

    /// # Errors
    /// Rejects unsafe production settings, invalid origins, and malformed values.
    pub fn from_values(values: &BTreeMap<String, String>) -> Result<Self, ConfigError> {
        let value = |name: &str| values.get(name).map(String::as_str);
        let production = match value("FLASHCARDS_ENVIRONMENT").unwrap_or("development") {
            "production" => true,
            "development" | "test" => false,
            _ => {
                return Err(ConfigError(
                    "FLASHCARDS_ENVIRONMENT must be development, test, or production",
                ));
            }
        };
        let session_secret = required_secret(values, "FLASHCARDS_SESSION_SECRET")?;
        let lookup_secret = required_secret(values, "FLASHCARDS_AUTH_LOOKUP_SECRET")?;
        if production && session_secret == lookup_secret {
            return Err(ConfigError(
                "Authentication secrets must differ in production",
            ));
        }
        if value("FLASHCARDS_AUTO_CREATE_SCHEMA").unwrap_or("false") != "false" {
            return Err(ConfigError(
                "FLASHCARDS_AUTO_CREATE_SCHEMA must be false; run explicit migrations",
            ));
        }
        let database_url = value("FLASHCARDS_DATABASE_URL")
            .ok_or(ConfigError("FLASHCARDS_DATABASE_URL is required"))?;
        if !["postgresql://", "postgres://", "postgresql+asyncpg://"]
            .iter()
            .any(|prefix| database_url.starts_with(prefix))
        {
            return Err(ConfigError("FLASHCARDS_DATABASE_URL must use PostgreSQL"));
        }
        let host = value("FLASHCARDS_HOST")
            .unwrap_or(if production { "0.0.0.0" } else { "127.0.0.1" })
            .parse()
            .map_err(|_| ConfigError("FLASHCARDS_HOST must be an IP address"))?;
        let port = value("FLASHCARDS_PORT")
            .unwrap_or("8000")
            .parse::<u16>()
            .map_err(|_| ConfigError("FLASHCARDS_PORT must be between 1 and 65535"))?;
        if port == 0 {
            return Err(ConfigError("FLASHCARDS_PORT must be between 1 and 65535"));
        }
        let session_ttl_seconds = value("FLASHCARDS_SESSION_TTL_SECONDS")
            .unwrap_or("2592000")
            .parse::<u32>()
            .map_err(|_| {
                ConfigError("FLASHCARDS_SESSION_TTL_SECONDS must be an integer of at least 300")
            })?;
        if session_ttl_seconds < 300 {
            return Err(ConfigError(
                "FLASHCARDS_SESSION_TTL_SECONDS must be at least 300",
            ));
        }
        let origins: Vec<String> = serde_json::from_str(
            value("FLASHCARDS_CORS_ORIGINS").unwrap_or("[]"),
        )
        .map_err(|_| ConfigError("FLASHCARDS_CORS_ORIGINS must be a JSON list of origins"))?;
        let cors_origins = origins
            .iter()
            .map(|origin| parse_origin(origin, production))
            .collect::<Result<_, _>>()?;
        Ok(Self {
            production,
            host,
            port,
            database_url: database_url.into(),
            session_secret,
            lookup_secret,
            bootstrap_password: value("FLASHCARDS_BOOTSTRAP_PASSWORD")
                .filter(|password| !password.is_empty())
                .map(str::to_owned),
            session_ttl_seconds,
            static_dir: PathBuf::from(
                value("FLASHCARDS_STATIC_DIR")
                    .unwrap_or("src/flashcards_generator/delivery/web/static/dist"),
            ),
            cors_origins,
        })
    }
}

fn required_secret(
    values: &BTreeMap<String, String>,
    name: &'static str,
) -> Result<String, ConfigError> {
    let secret = values.get(name).ok_or(ConfigError(name))?;
    if secret.chars().count() < 32 {
        return Err(ConfigError(name));
    }
    Ok(secret.clone())
}

fn parse_origin(origin: &str, production: bool) -> Result<HeaderValue, ConfigError> {
    let invalid = || {
        ConfigError(
            "CORS origins require a scheme and host, HTTPS in production, and no path or credentials",
        )
    };
    let header = HeaderValue::from_str(origin).map_err(|_| invalid())?;
    let uri: Uri = origin.parse().map_err(|_| invalid())?;
    if !matches!(uri.scheme_str(), Some("http" | "https"))
        || (production && uri.scheme_str() != Some("https"))
        || uri
            .authority()
            .is_none_or(|authority| authority.as_str().contains('@'))
        || uri.path() != "/"
        || uri.query().is_some()
        || origin.ends_with('/')
    {
        return Err(invalid());
    }
    Ok(header)
}

#[cfg(test)]
mod tests;
