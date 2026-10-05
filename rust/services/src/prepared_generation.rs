use crate::{
    document_preparation::{DocumentPreparer, PreparedDocuments},
    generation::{GenerationError, GenerationResult, generate_named_document, valid_input},
    generation_options::GenerationOptions,
    notebooklm::{NotebookLMGateway, TemporaryNotebookFactory},
};
use flashcards_domain::Deck;
use std::{error::Error, fmt, path::Path, time::Duration};

#[derive(Debug)]
pub enum PreparedGenerationError<P, E> {
    InvalidInput,
    Preparation(P),
    NoContent,
    NoCards {
        cleanup_errors: Vec<E>,
    },
    Generation {
        completed: Vec<GenerationResult<E>>,
        failed_chunk: usize,
        error: GenerationError<E>,
    },
}
impl<P: Error, E: Error> fmt::Display for PreparedGenerationError<P, E> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidInput => "Invalid document generation input",
            Self::Preparation(_) => "Local document preparation failed",
            Self::NoContent => "No relevant document content",
            Self::NoCards { .. } => "No flashcards generated from the document",
            Self::Generation { .. } => "Document chunk generation failed",
        })
    }
}
impl<P: Error + 'static, E: Error + 'static> Error for PreparedGenerationError<P, E> {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Preparation(error) => Some(error),
            Self::Generation { error, .. } => Some(error),
            _ => None,
        }
    }
}

pub struct PreparedGenerationResult<E> {
    pub deck: Deck,
    pub cleanup_errors: Vec<E>,
    pub quality_stats: Option<flashcards_engines::quality::QualityStats>,
}

/// # Errors
/// Rejects invalid or empty input and propagates preparation, generation, and cleanup failures.
pub async fn generate_prepared_document<P: DocumentPreparer, F: TemporaryNotebookFactory>(
    preparer: &P,
    factory: &F,
    path: &Path,
    name: &str,
    options: &GenerationOptions,
) -> Result<
    PreparedGenerationResult<<F::Gateway as NotebookLMGateway>::Error>,
    PreparedGenerationError<P::Error, <F::Gateway as NotebookLMGateway>::Error>,
> {
    generate_prepared_document_with_progress(
        preparer,
        factory,
        path,
        name,
        options,
        &mut |_, _, _| {},
    )
    .await
}

/// # Errors
/// Rejects invalid or empty input and retains completed chunks when later generation fails.
pub async fn generate_prepared_document_with_progress<
    P: DocumentPreparer,
    F: TemporaryNotebookFactory,
>(
    preparer: &P,
    factory: &F,
    path: &Path,
    name: &str,
    options: &GenerationOptions,
    progress: &mut (impl FnMut(&Deck, usize, usize) + Send),
) -> Result<
    PreparedGenerationResult<<F::Gateway as NotebookLMGateway>::Error>,
    PreparedGenerationError<P::Error, <F::Gateway as NotebookLMGateway>::Error>,
> {
    if !valid_input(path, name) {
        return Err(PreparedGenerationError::InvalidInput);
    }
    let documents = preparer
        .prepare(path)
        .await
        .map_err(PreparedGenerationError::Preparation)?;
    if documents.files().is_empty() {
        return Err(PreparedGenerationError::NoContent);
    }
    let total = documents.files().len();
    let mut completed = Vec::new();
    for (index, file) in documents.files().iter().enumerate() {
        let suffix = format!("_chunk{}", index + 1);
        let title = if total == 1 {
            name.to_owned()
        } else {
            name.chars().take(1000 - suffix.len()).collect::<String>() + &suffix
        };
        let generated = if total > 1 {
            generate_chunk(factory, file, &title, name, options, (index + 1, total)).await
        } else {
            generate_named_document(factory, file, &title, name, options, None).await
        };
        let mut part = match generated {
            Ok(part) => part,
            Err(error) => {
                return Err(PreparedGenerationError::Generation {
                    completed,
                    failed_chunk: index + 1,
                    error,
                });
            }
        };
        if total > 1 {
            part.deck.name = title;
            part.deck.description = format!("Chunk {} of {total}", index + 1);
        }
        progress(&part.deck, index + 1, total);
        completed.push(part);
        if index + 1 < total {
            factory.wait(Duration::from_secs(5)).await;
        }
    }
    let mut deck = Deck::new(name.to_owned());
    deck.description = if total == 1 {
        format!("Deck de {name}")
    } else {
        format!("Deck de {name} ({total} chunks)")
    };
    let mut cleanup_errors = Vec::new();
    for part in completed {
        if total == 1 {
            deck.notebook_id = part.deck.notebook_id;
        }
        deck.flashcards.extend(part.deck.flashcards);
        if let Some(error) = part.cleanup_error {
            cleanup_errors.push(error);
        }
    }
    deck.deduplicate_standard();
    let quality_stats =
        (total > 1).then(|| flashcards_engines::quality::filter(&mut deck.flashcards));
    if deck.flashcards.is_empty() {
        return Err(PreparedGenerationError::NoCards { cleanup_errors });
    }
    Ok(PreparedGenerationResult {
        deck,
        cleanup_errors,
        quality_stats,
    })
}

async fn generate_chunk<F: TemporaryNotebookFactory>(
    factory: &F,
    path: &Path,
    title: &str,
    name: &str,
    options: &GenerationOptions,
    context: (usize, usize),
) -> Result<
    GenerationResult<<F::Gateway as NotebookLMGateway>::Error>,
    GenerationError<<F::Gateway as NotebookLMGateway>::Error>,
> {
    let mut attempt = 0;
    loop {
        let result =
            generate_named_document(factory, path, title, name, options, Some(context)).await;
        let retry = matches!(&result, Err(GenerationError::Provider(error)) if factory.is_transient_error(error));
        if !retry || attempt == 2 {
            return result;
        }
        factory.wait(Duration::from_secs(5 << attempt)).await;
        attempt += 1;
    }
}
