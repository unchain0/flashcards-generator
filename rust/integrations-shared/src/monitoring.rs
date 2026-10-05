use sentry::{
    ClientInitGuard, ClientOptions,
    protocol::{Event, Exception, Frame, Stacktrace},
};
use std::{env::VarError, error::Error, fmt, time::Duration};

#[derive(Clone, Copy)]
pub enum Service {
    Web,
    Companion,
}
impl Service {
    #[must_use]
    pub fn name(self) -> &'static str {
        match self {
            Self::Web => "flashcards-web",
            Self::Companion => "flashcards-companion",
        }
    }
    fn dsn(self) -> &'static str {
        match self {
            Self::Web => {
                "https://3671d9736ad0485d62fc74ba8897c9cb@o4505598204248064.ingest.us.sentry.io/4512186402078720"
            }
            Self::Companion => {
                "https://bc276d57f13bcea95600a433fe8df3a4@o4505598204248064.ingest.us.sentry.io/4512186407256064"
            }
        }
    }
    fn variable(self) -> &'static str {
        match self {
            Self::Web => "FLASHCARDS_WEB_SENTRY_DSN",
            Self::Companion => "FLASHCARDS_COMPANION_SENTRY_DSN",
        }
    }
}

#[derive(Debug)]
pub struct InvalidMonitoringConfig;
impl fmt::Display for InvalidMonitoringConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Invalid Sentry configuration")
    }
}
impl Error for InvalidMonitoringConfig {}

/// # Errors
/// Rejects invalid monitoring configuration.
pub fn initialize(service: Service) -> Result<ClientInitGuard, InvalidMonitoringConfig> {
    let dsn = match environment_value(service.variable())? {
        Some(value) => value,
        None => environment_value("SENTRY_DSN")?.unwrap_or_else(|| service.dsn().to_owned()),
    };
    let url = reqwest::Url::parse(&dsn).map_err(|_| InvalidMonitoringConfig)?;
    if url.scheme() != "https" {
        return Err(InvalidMonitoringConfig);
    }
    let enabled = match environment_value("FLASHCARDS_SENTRY_ENABLED")?.as_deref() {
        Some("false" | "0") => false,
        Some("true" | "1") | None => true,
        _ => return Err(InvalidMonitoringConfig),
    };
    let environment = environment_value("SENTRY_ENVIRONMENT")?.unwrap_or_else(|| {
        if cfg!(debug_assertions) {
            "development".into()
        } else {
            "production".into()
        }
    });
    if !matches!(
        environment.as_str(),
        "development" | "production" | "staging" | "sentry-validation"
    ) {
        return Err(InvalidMonitoringConfig);
    }
    let mut options = ClientOptions::new()
        .default_integrations(false)
        .send_default_pii(false)
        .sample_rate(if enabled { 1.0 } else { 0.0 })
        .traces_sample_rate(0.0)
        .max_breadcrumbs(0)
        .attach_stacktrace(true)
        .release(concat!("flashcards-generator@", env!("CARGO_PKG_VERSION")))
        .environment(environment)
        .shutdown_timeout(Duration::from_secs(5))
        .add_integration(sentry::integrations::panic::PanicIntegration::default())
        .add_integration(sentry::integrations::backtrace::AttachStacktraceIntegration)
        .before_send(move |event| Some(sanitize(event, service)));
    options.dsn = Some(dsn.parse().map_err(|_| InvalidMonitoringConfig)?);
    options.auto_session_tracking = false;
    Ok(sentry::init(options))
}

fn environment_value(name: &str) -> Result<Option<String>, InvalidMonitoringConfig> {
    match std::env::var(name) {
        Ok(value) => Ok(Some(value)),
        Err(VarError::NotPresent) => Ok(None),
        Err(VarError::NotUnicode(_)) => Err(InvalidMonitoringConfig),
    }
}

pub fn report_error(error: &(dyn Error + 'static)) {
    sentry::capture_error(error);
}

#[must_use]
pub fn validation_event() -> String {
    sentry::capture_message("Synthetic Sentry validation", sentry::Level::Error).to_string()
}

fn sanitize(event: Event<'static>, service: Service) -> Event<'static> {
    let mut clean = Event {
        event_id: event.event_id,
        timestamp: event.timestamp,
        level: event.level,
        platform: "native".into(),
        release: event.release,
        environment: event.environment,
        message: Some("Rust application error".into()),
        stacktrace: event.stacktrace.map(clean_stack),
        exception: event
            .exception
            .into_iter()
            .map(|exception| Exception {
                ty: "RustError".into(),
                stacktrace: exception.stacktrace.map(clean_stack),
                ..Exception::default()
            })
            .collect(),
        ..Event::default()
    };
    clean.tags.insert("service".into(), service.name().into());
    clean
}

fn clean_stack(stack: Stacktrace) -> Stacktrace {
    Stacktrace {
        frames: stack
            .frames
            .into_iter()
            .map(|frame| Frame {
                function: frame.function,
                module: frame.module,
                filename: frame
                    .filename
                    .and_then(|filename| filename.rsplit(['/', '\\']).next().map(str::to_owned)),
                lineno: frame.lineno,
                colno: frame.colno,
                in_app: frame.in_app,
                ..Frame::default()
            })
            .collect(),
        ..Stacktrace::default()
    }
}

#[cfg(test)]
mod tests;
