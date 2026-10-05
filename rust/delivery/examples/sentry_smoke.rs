use flashcards_integrations::{
    logging,
    monitoring::{self, Service},
};
use std::error::Error;

fn main() -> Result<(), Box<dyn Error>> {
    let service = match std::env::args().nth(1).as_deref() {
        Some("web") => Service::Web,
        Some("companion") => Service::Companion,
        _ => return Err("Select web or companion".into()),
    };
    logging::initialize();
    let _guard = monitoring::initialize(service)?;
    let event_id = monitoring::validation_event();
    tracing::info!(service = service.name(), %event_id, "Synthetic Sentry validation submitted");
    Ok(())
}
