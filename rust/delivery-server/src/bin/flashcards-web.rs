use flashcards_delivery_server::{
    web::{self, WebState},
    web_config::WebConfig,
};
use flashcards_integrations_server::{
    auth_crypto::AuthCrypto, logging, monitoring, web_database::WebDatabase,
};
use std::{error::Error, net::SocketAddr};

fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    logging::initialize();
    let _sentry = monitoring::initialize(monitoring::Service::Web)?;
    let result = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(run());
    if let Err(error) = &result {
        monitoring::report_error(error.as_ref());
    }
    result
}

async fn run() -> Result<(), Box<dyn Error + Send + Sync>> {
    let config = WebConfig::from_env()?;
    let crypto = AuthCrypto::new(&config.session_secret, &config.lookup_secret)?;
    let database = WebDatabase::connect(&config.database_url, crypto).await?;
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    match arguments.as_slice() {
        [argument] if argument == "--migrate" => {
            database.migrate().await?;
            database.check().await?;
            tracing::info!("Database migrations complete");
        }
        [argument] if argument == "--provision-user" => {
            database.check().await?;
            let password = std::env::var("FLASHCARDS_PROVISION_PASSWORD")
                .map_err(|_| "FLASHCARDS_PROVISION_PASSWORD must contain a UTF-8 password")?;
            database.create_user(&password).await?;
            tracing::info!("Access password provisioned");
        }
        [] => {
            database.check().await?;
            database
                .ensure_bootstrap(config.bootstrap_password.as_deref())
                .await?;
            let state = WebState::new(
                database.clone(),
                config.production,
                config.session_ttl_seconds,
            );
            let app = web::router(state, &config.static_dir, config.cors_origins)?;
            let listener =
                tokio::net::TcpListener::bind(SocketAddr::new(config.host, config.port)).await?;
            tracing::info!(address = %listener.local_addr()?, "Hosted web server started");
            flashcards_delivery_server::server::serve(listener, app).await?;
        }
        _ => {
            return Err(
                "Use flashcards-web, flashcards-web --migrate, or flashcards-web --provision-user"
                    .into(),
            );
        }
    }
    database.close().await;
    Ok(())
}
