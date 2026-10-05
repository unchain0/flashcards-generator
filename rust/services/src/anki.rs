use flashcards_domain::Deck;
use std::{error::Error, future::Future};

pub trait AnkiGateway: Send + Sync {
    type Error: Error + Send + Sync + 'static;
    fn version(&self) -> impl Future<Output = Result<u64, Self::Error>> + Send;
    fn deck_names(&self) -> impl Future<Output = Result<Vec<String>, Self::Error>> + Send;
    fn find_notes(&self, query: &str)
    -> impl Future<Output = Result<Vec<u64>, Self::Error>> + Send;
    fn model_field_names(
        &self,
        model: &str,
    ) -> impl Future<Output = Result<Vec<String>, Self::Error>> + Send;
    fn export_cloze(
        &self,
        deck: &Deck,
        destination: &str,
    ) -> impl Future<Output = Result<Vec<u64>, Self::Error>> + Send;
}
