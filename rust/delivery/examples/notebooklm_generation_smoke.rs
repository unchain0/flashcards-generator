use flashcards_integrations::{logging, notebooklm::NotebookLMClient};
use flashcards_services::{
    generation::DEFAULT_INSTRUCTIONS,
    generation_options::GenerationOptions,
    notebooklm::{NotebookLMGateway, TemporaryNotebook, TemporaryNotebookFactory},
};
use std::{collections::BTreeSet, error::Error, path::PathBuf, time::Duration};

const SOURCE: &str = concat!(
    "Synthetic validation source: cell biology. ",
    "The plasma membrane separates the cell interior from its environment. ",
    "The plasma membrane consists of a phospholipid bilayer containing proteins. ",
    "Selective permeability controls which substances cross the plasma membrane. ",
    "The nucleus contains the genetic material of a eukaryotic cell. ",
    "Ribosomes synthesize proteins by translating messenger RNA. ",
    "Mitochondria produce ATP during aerobic cellular respiration. ",
    "Chloroplasts carry out photosynthesis in plant cells. ",
    "The plant cell wall contains cellulose and provides mechanical support. ",
    "Lysosomes contain enzymes that degrade cellular components. ",
    "The Golgi apparatus modifies, sorts, and packages proteins. ",
    "The rough endoplasmic reticulum has attached ribosomes. ",
    "The smooth endoplasmic reticulum participates in lipid synthesis. ",
    "The cytoskeleton maintains cell shape and supports intracellular transport. ",
    "Prokaryotic cells do not have a membrane-bound nucleus."
);

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    logging::initialize();
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    let [storage] = arguments.as_slice() else {
        return Err("Provide one local storage-state file; this check creates and removes a synthetic QA notebook".into());
    };
    let client = NotebookLMClient::from_storage_file(&PathBuf::from(storage))
        .await
        .map_err(|_| "NotebookLM authentication failed")?;
    let before: BTreeSet<_> = client
        .list_notebooks()
        .await
        .map_err(|_| "NotebookLM listing failed")?
        .into_iter()
        .map(|notebook| notebook.id)
        .collect();
    let mut scope = client
        .create_temporary_notebook("FLASHCARDS-RUST-SYNTHETIC-QA")
        .await
        .map_err(|_| "QA notebook creation failed")?;
    let owned = scope.notebook().id.clone();
    let options: GenerationOptions = serde_json::from_value(
        serde_json::json!({"quantity":"fewer","timeout":180,"single_cloze":true}),
    )?;
    let generated = tokio::time::timeout(Duration::from_secs(300), async {
        tracing::info!("Adding synthetic QA text source");
        let source = client
            .add_text_source(&owned, "Synthetic cell biology", SOURCE)
            .await?;
        client
            .wait_for_source(&owned, &source, Duration::from_secs(60))
            .await?;
        tracing::info!("Generating synthetic QA flashcards");
        let artifact = client
            .generate_flashcards(&owned, &[source], DEFAULT_INSTRUCTIONS, &options)
            .await?;
        client
            .wait_for_artifact(&owned, &artifact.id, Duration::from_secs(180))
            .await?;
        client.download_flashcards(&owned, &artifact.id).await
    })
    .await;
    let cleaned = scope.close().await;
    drop(scope);
    let deferred_cleaned = client.finish_cleanup().await;
    if cleaned.is_err() || !deferred_cleaned {
        return Err("QA notebook cleanup could not be confirmed".into());
    }
    let after: BTreeSet<_> = client
        .list_notebooks()
        .await
        .map_err(|_| "QA cleanup listing failed")?
        .into_iter()
        .map(|notebook| notebook.id)
        .collect();
    if !before.is_subset(&after) || after.contains(&owned) {
        return Err("Existing notebook preservation or QA cleanup check failed".into());
    }
    match generated {
        Ok(Ok(cards)) if !cards.is_empty() => tracing::info!(
            card_count = cards.len(),
            "Native NotebookLM synthetic generation and cleanup passed"
        ),
        Ok(Err(error)) => {
            tracing::error!(error = %error, "Native NotebookLM synthetic generation failed after cleanup");
            return Err("Synthetic generation failed".into());
        }
        _ => return Err("Synthetic generation timed out or returned no cards".into()),
    }
    Ok(())
}
