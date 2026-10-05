use flashcards_domain::Deck;
use std::{error::Error, path::Path};

pub trait DeckExporter: Send + Sync {
    type Error: Error + Send + Sync + 'static;

    /// # Errors
    /// Propagates CSV encoding and output persistence errors.
    fn export_csv(&self, deck: &Deck, path: &Path) -> Result<(), Self::Error>;
}
