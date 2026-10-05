use crate::{
    deck_exporter::DeckExporter,
    document_inputs::SourceFilename,
    document_preparation::DocumentPreparer,
    generation::GenerationError,
    generation_options::GenerationOptions,
    local_jobs::{JobStatus, SourceOutcome},
    notebooklm::{NotebookLMGateway, TemporaryNotebookFactory},
    prepared_generation::{
        PreparedGenerationError, PreparedGenerationResult, generate_prepared_document_with_progress,
    },
};
use std::{error::Error, fmt, path::Path};

#[derive(Debug)]
pub enum ExportIssue<P, G, X> {
    Generation(PreparedGenerationError<P, G>),
    Cleanup(G),
    Export(X),
    QualityLimit,
}
impl<P: Error, G: Error, X: Error> fmt::Display for ExportIssue<P, G, X> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Generation(_) => "Document generation failed",
            Self::Cleanup(_) => "Temporary notebook cleanup failed",
            Self::Export(_) => "Local CSV export failed",
            Self::QualityLimit => "Quality analysis reached the maximum pair limit",
        })
    }
}
impl<P: Error + 'static, G: Error + 'static, X: Error + 'static> Error for ExportIssue<P, G, X> {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Generation(error) => Some(error),
            Self::Cleanup(error) => Some(error),
            Self::Export(error) => Some(error),
            Self::QualityLimit => None,
        }
    }
}
pub struct DocumentExportResult<P, G, X> {
    pub outcome: SourceOutcome,
    pub artifacts: Vec<String>,
    pub issues: Vec<ExportIssue<P, G, X>>,
}

impl<P, G, X> DocumentExportResult<P, G, X> {
    fn record_warnings(&mut self, generated: &mut PreparedGenerationResult<G>) {
        if generated
            .quality_stats
            .as_ref()
            .is_some_and(|stats| stats.truncated)
        {
            self.issues.push(ExportIssue::QualityLimit);
        }
        self.issues
            .extend(generated.cleanup_errors.drain(..).map(ExportIssue::Cleanup));
    }

    fn record_failure(&mut self, error: PreparedGenerationError<P, G>) {
        self.outcome = SourceOutcome::Failed;
        match error {
            PreparedGenerationError::NoCards { cleanup_errors } => {
                self.issues
                    .push(ExportIssue::Generation(PreparedGenerationError::NoCards {
                        cleanup_errors: Vec::new(),
                    }));
                self.issues
                    .extend(cleanup_errors.into_iter().map(ExportIssue::Cleanup));
            }
            PreparedGenerationError::Generation {
                completed,
                failed_chunk,
                error,
            } => {
                let (error, cleanup) = match error {
                    GenerationError::Cleanup {
                        generation,
                        cleanup,
                    } => (*generation, Some(cleanup)),
                    error => (error, None),
                };
                self.issues.push(ExportIssue::Generation(
                    PreparedGenerationError::Generation {
                        completed: Vec::new(),
                        failed_chunk,
                        error,
                    },
                ));
                self.issues.extend(
                    completed
                        .into_iter()
                        .filter_map(|part| part.cleanup_error)
                        .map(ExportIssue::Cleanup),
                );
                self.issues
                    .extend(cleanup.into_iter().map(ExportIssue::Cleanup));
            }
            error => self.issues.push(ExportIssue::Generation(error)),
        }
    }
}

pub struct DocumentBatch<'a> {
    pub input_dir: &'a Path,
    pub output_dir: &'a Path,
    pub filenames: &'a [SourceFilename],
    pub options: &'a GenerationOptions,
}

/// # Errors
/// Propagates report callback errors; individual document failures remain in reported results.
pub async fn generate_batch_csv<
    P: DocumentPreparer,
    F: TemporaryNotebookFactory,
    X: DeckExporter,
    R,
>(
    preparer: &P,
    factory: &F,
    exporter: &X,
    batch: DocumentBatch<'_>,
    mut report: impl FnMut(
        DocumentExportResult<P::Error, <F::Gateway as NotebookLMGateway>::Error, X::Error>,
    ) -> Result<(), R>,
) -> Result<JobStatus, R> {
    let mut status = JobStatus::Completed;
    for filename in batch.filenames {
        let result = generate_document_csv(
            preparer,
            factory,
            exporter,
            batch.input_dir,
            batch.output_dir,
            filename,
            batch.options,
        )
        .await;
        if result.outcome == SourceOutcome::Failed {
            status = JobStatus::Failed;
        }
        report(result)?;
    }
    Ok(status)
}

pub async fn generate_document_csv<
    P: DocumentPreparer,
    F: TemporaryNotebookFactory,
    X: DeckExporter,
>(
    preparer: &P,
    factory: &F,
    exporter: &X,
    input_dir: &Path,
    output_dir: &Path,
    filename: &SourceFilename,
    options: &GenerationOptions,
) -> DocumentExportResult<P::Error, <F::Gateway as NotebookLMGateway>::Error, X::Error> {
    let stem = filename.stem();
    let mut result = DocumentExportResult {
        outcome: SourceOutcome::Completed,
        artifacts: Vec::new(),
        issues: Vec::new(),
    };
    let generation = generate_prepared_document_with_progress(
        preparer,
        factory,
        &input_dir.join(filename.as_str()),
        stem,
        options,
        &mut |deck, index, total| {
            if total > 1 && !deck.flashcards.is_empty() {
                export_deck(
                    exporter,
                    output_dir,
                    format!("{stem}_chunk{index}.csv"),
                    deck,
                    &mut result,
                );
            }
        },
    )
    .await;
    match generation {
        Ok(mut generated) => {
            result.record_warnings(&mut generated);
            export_deck(
                exporter,
                output_dir,
                format!("{stem}.csv"),
                &generated.deck,
                &mut result,
            );
        }
        Err(PreparedGenerationError::NoContent) => {
            result.outcome = SourceOutcome::Skipped;
            return result;
        }
        Err(error) => result.record_failure(error),
    }
    result
}

fn export_deck<P, G, X: DeckExporter>(
    exporter: &X,
    output_dir: &Path,
    name: String,
    deck: &flashcards_domain::Deck,
    result: &mut DocumentExportResult<P, G, X::Error>,
) {
    match exporter.export_csv(deck, &output_dir.join(&name)) {
        Ok(()) => result.artifacts.push(name),
        Err(error) => {
            result.outcome = SourceOutcome::Failed;
            result.issues.push(ExportIssue::Export(error));
        }
    }
}

#[cfg(test)]
mod tests;
