use flashcards_integrations::{logging, notebooklm::NotebookLMClient, process::run_bounded};
use flashcards_services::notebooklm::NotebookLMGateway;
use std::{collections::BTreeSet, error::Error, path::PathBuf, time::Duration};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    logging::initialize();
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    let (storage, format, browser) = match arguments.as_slice() {
        [storage] => (storage, "pdf", None),
        [storage, format] if format == "pdf" => (storage, "pdf", None),
        [storage, format] if format == "pptx" => (storage, "pptx", None),
        [storage, format, browser] if format == "pdf" => (storage, "pdf", Some(browser)),
        _ => {
            return Err(
                "Provide a local NotebookLM storage-state file, optional pdf or pptx format, and an optional dedicated browser profile for pdf"
                    .into(),
            );
        }
    };
    let client = NotebookLMClient::from_storage_file(&PathBuf::from(storage))
        .await
        .map_err(|_| "NotebookLM authentication failed")?;
    let before = client
        .list_notebooks()
        .await
        .map_err(|_| "NotebookLM listing failed")?;
    let existing: BTreeSet<_> = before.into_iter().map(|notebook| notebook.id).collect();
    tracing::info!(
        format,
        "Running live native Companion browser validation with a synthetic document"
    );
    let mut command = tokio::process::Command::new("bash");
    command
        .arg("scripts/rust-live-companion-check.sh")
        .arg(storage)
        .arg(format);
    if let Some(browser) = browser {
        command.arg(browser);
    }
    let output = run_bounded(&mut command, Duration::from_mins(15), 1024 * 1024).await;
    let outcome = match output {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => {
            tracing::error!(stdout = %String::from_utf8_lossy(&output.stdout), stderr = %String::from_utf8_lossy(&output.stderr), "Live Companion validation failed");
            Err("Live Companion validation failed")
        }
        Err(_) => Err("Live Companion validation process failed"),
    };
    let after = client
        .list_notebooks()
        .await
        .map_err(|_| "NotebookLM cleanup listing failed")?;
    let retained: BTreeSet<_> = after.iter().map(|notebook| notebook.id.clone()).collect();
    if !existing.is_subset(&retained)
        || after.iter().any(|notebook| {
            !existing.contains(&notebook.id)
                && matches!(
                    notebook.title.as_str(),
                    "FLASHCARDS-RUST-PDF-QA"
                        | "01-FLASHCARDS-RUST-PDF-QA"
                        | "FLASHCARDS-RUST-PPTX-QA"
                        | "01-FLASHCARDS-RUST-PPTX-QA"
                )
        })
    {
        return Err("Notebook preservation or synthetic QA cleanup could not be confirmed".into());
    }
    outcome?;
    tracing::info!("Native Companion browser generation and notebook preservation passed");
    Ok(())
}
