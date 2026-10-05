use flashcards_integrations::{
    anki::{AnkiConnectClient, DEFAULT_ANKI_CONNECT_URL},
    logging,
};
use flashcards_services::anki::AnkiGateway;
use std::error::Error;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    logging::initialize();
    let client = AnkiConnectClient::new(
        DEFAULT_ANKI_CONNECT_URL,
        std::env::var("FLASHCARDS_ANKI_API_KEY").ok(),
    )?;
    let version = client.version().await?;
    let decks = client.deck_names().await?;
    tracing::info!(
        version,
        deck_count = decks.len(),
        "Local AnkiConnect read-only validation passed"
    );
    Ok(())
}
