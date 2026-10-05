pub fn initialize() {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    tracing_subscriber::registry()
        .with(filter)
        .with(
            tracing_subscriber::fmt::layer()
                .with_writer(std::io::stderr)
                .with_target(false),
        )
        .with(
            sentry::integrations::tracing::layer()
                .event_filter(|metadata| {
                    if *metadata.level() <= tracing::Level::WARN {
                        sentry::integrations::tracing::EventFilter::Event
                    } else {
                        sentry::integrations::tracing::EventFilter::Ignore
                    }
                })
                .span_filter(|_| false),
        )
        .init();
}
use tracing_subscriber::prelude::*;
