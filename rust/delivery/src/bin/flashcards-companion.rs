use flashcards_delivery::companion::{self, CompanionState};
use flashcards_integrations::{
    companion_auth::{CompanionAuthError, RemoteTokenVerifier},
    logging, monitoring,
    notebooklm_browser::NotebookLMBrowserLogin,
    notebooklm_profiles::LocalNotebookLMProfiles,
};
use std::{error::Error, path::PathBuf};

fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    logging::initialize();
    let _sentry = monitoring::initialize(monitoring::Service::Companion)?;
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
    if std::env::args_os().len() != 1 {
        return Err(
            "Use flashcards-companion with FLASHCARDS_COMPANION_WEB_ORIGIN configured".into(),
        );
    }
    let origin = std::env::var("FLASHCARDS_COMPANION_WEB_ORIGIN")
        .map_err(|_| CompanionAuthError::InvalidOrigin)?;
    let verifier = RemoteTokenVerifier::new(&origin)?;
    let profiles = LocalNotebookLMProfiles::new(data_dir()?)?;
    let browser = NotebookLMBrowserLogin::new(
        std::env::var_os("FLASHCARDS_COMPANION_BROWSER").map(PathBuf::from),
    );
    let jobs = flashcards_integrations::local_job_store::LocalJobStore::new()?;
    let worker = flashcards_delivery::companion_worker::CompanionWorker::start(
        jobs.clone(),
        profiles.clone(),
    );
    let app = companion::router(CompanionState::new(
        verifier,
        profiles,
        browser,
        jobs.clone(),
    ))?;
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 8766)).await?;
    tracing::info!(address = %listener.local_addr()?, "Local Companion started");
    let result = flashcards_delivery::server::serve(listener, app).await;
    worker.shutdown().await??;
    jobs.close()?;
    result?;
    Ok(())
}

fn data_dir() -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
    if let Some(path) = std::env::var_os("FLASHCARDS_COMPANION_DATA_DIR") {
        return Ok(PathBuf::from(path));
    }
    #[cfg(target_os = "windows")]
    let base = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is missing")?);
    #[cfg(target_os = "macos")]
    let base = PathBuf::from(std::env::var_os("HOME").ok_or("HOME is missing")?)
        .join("Library/Application Support");
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let base = match std::env::var_os("XDG_DATA_HOME")
        .filter(|value| PathBuf::from(value).is_absolute())
    {
        Some(path) => PathBuf::from(path),
        None => {
            PathBuf::from(std::env::var_os("HOME").ok_or("HOME is missing")?).join(".local/share")
        }
    };
    Ok(base.join("flashcards-generator/companion"))
}
