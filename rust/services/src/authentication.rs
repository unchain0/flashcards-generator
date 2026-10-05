use flashcards_domain::identity::UserId;
use std::{error::Error, future::Future};

pub trait CompanionTokenVerifier: Send + Sync {
    type Error: Error + Send + Sync + 'static;

    fn verify(
        &self,
        token: &str,
    ) -> impl Future<Output = Result<Option<UserId>, Self::Error>> + Send;
}

pub trait WebAuthStore: Send + Sync {
    type Error: Error + Send + Sync + 'static;

    fn authenticate_and_create_session(
        &self,
        password: &str,
        ttl_seconds: u32,
    ) -> impl Future<Output = Result<Option<String>, Self::Error>> + Send;

    fn delete_session(
        &self,
        token: Option<&str>,
    ) -> impl Future<Output = Result<(), Self::Error>> + Send;

    /// # Errors
    /// Propagates session lookup errors from the store.
    fn user_for_session(
        &self,
        token: Option<&str>,
    ) -> impl Future<Output = Result<Option<String>, Self::Error>> + Send;

    /// # Errors
    /// Propagates capability creation errors from the store.
    fn create_companion_token(
        &self,
        token: Option<&str>,
    ) -> impl Future<Output = Result<Option<String>, Self::Error>> + Send;

    /// # Errors
    /// Propagates capability lookup errors from the store.
    fn user_for_companion_token(
        &self,
        token: &str,
    ) -> impl Future<Output = Result<Option<String>, Self::Error>> + Send;
}

pub struct WebAuthentication<S> {
    store: S,
    session_ttl_seconds: u32,
}

impl<S: WebAuthStore> WebAuthentication<S> {
    pub fn new(store: S, session_ttl_seconds: u32) -> Self {
        Self {
            store,
            session_ttl_seconds,
        }
    }

    /// # Errors
    /// Propagates authentication and session persistence errors from the store.
    pub async fn login(&self, password: &str) -> Result<Option<String>, S::Error> {
        self.store
            .authenticate_and_create_session(password, self.session_ttl_seconds)
            .await
    }

    /// # Errors
    /// Propagates session revocation errors from the store.
    pub async fn logout(&self, token: Option<&str>) -> Result<(), S::Error> {
        self.store.delete_session(token).await
    }

    /// # Errors
    /// Propagates session lookup errors from the store.
    pub async fn user_for_session(&self, token: Option<&str>) -> Result<Option<String>, S::Error> {
        self.store.user_for_session(token).await
    }

    /// # Errors
    /// Propagates capability creation errors from the store.
    pub async fn create_companion_token(
        &self,
        token: Option<&str>,
    ) -> Result<Option<String>, S::Error> {
        self.store.create_companion_token(token).await
    }

    /// # Errors
    /// Propagates capability lookup errors from the store.
    pub async fn user_for_companion_token(&self, token: &str) -> Result<Option<String>, S::Error> {
        self.store.user_for_companion_token(token).await
    }
}
