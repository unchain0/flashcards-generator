use crate::{
    generation_options::GenerationOptions,
    notebooklm::{NotebookLMGateway, TemporaryNotebook, TemporaryNotebookFactory},
};
use flashcards_domain::{Deck, Flashcard};
use flashcards_engines::cloze;
use std::{error::Error, fmt, path::Path, time::Duration};

pub const DEFAULT_INSTRUCTIONS: &str = concat!(
    "Crie flashcards para recuperação ativa e repetição espaçada usando ",
    "somente informações explicitamente sustentadas pela fonte. ",
    "SELEÇÃO: priorize fundamentos, definições, relações causais, condições, ",
    "distinções e etapas essenciais. Ignore títulos, repetições, detalhes ",
    "decorativos, opiniões e trechos incompletos. Não tente cobrir todo o texto. ",
    "FORMATO OBRIGATÓRIO: use apenas Cloze Deletion. A frente deve ser uma ",
    "frase declarativa natural com exatamente uma lacuna {{c1::resposta}}. ",
    "Exemplo: 'A {{c1::mitocôndria}} produz a maior parte do ATP celular.' ",
    "QUALIDADE DE CADA CARD: ",
    "1. Teste uma única ideia independente. Divida frases com mais de um fato. ",
    "2. A lacuna deve ocultar a menor resposta significativa possível, ",
    "preferencialmente de uma a cinco palavras; nunca oculte palavras triviais. ",
    "3. Depois de ocultar a resposta, a frase deve continuar auto-contida e ",
    "permitir uma única resposta esperada. Inclua o qualificador mínimo que ",
    "elimine ambiguidades e interferência com conceitos semelhantes. ",
    "4. Não deixe na frente sinônimos, traduções, paráfrases ou pistas ",
    "gramaticais que revelem a resposta. Use {{c1::termo::dica}} apenas quando ",
    "uma dica curta for indispensável para tornar a pergunta inequívoca. ",
    "5. Mantenha a frente curta, idealmente até 25 palavras, sem perder o ",
    "contexto necessário. O verso deve trazer apenas uma explicação breve ",
    "do porquê, mecanismo ou contexto já presente na fonte. ",
    "6. Evite listas. Converta cada item em uma relação significativa própria. ",
    "Se a ordem for essencial, teste uma etapa por card e mantenha visível ",
    "apenas o contexto necessário para localizar essa etapa. Nunca agrupe uma ",
    "lista inteira em clozes c1, c2, c3. ",
    "7. Para conceitos parecidos, formule pistas que destaquem a diferença ",
    "diagnóstica em vez de criar cartões quase idênticos e ambíguos. ",
    "8. Para código, oculte apenas o identificador, operador ou expressão-chave; ",
    "nunca blocos inteiros. Para matemática, preserve a notação em LaTeX $...$. ",
    "9. Não invente exemplos, relações, definições ou conclusões. Use exemplos ",
    "somente quando estiverem na fonte e forem necessários para compreensão. ",
    "10. Não gere duplicatas nem cartões que possam ser respondidos apenas por ",
    "senso comum, estrutura da frase ou reconhecimento superficial. ",
    "CONTEXTO DO DOCUMENTO: trabalhe somente com o conteúdo completo desta ",
    "seção. Se um conceito depender de outra parte ou não estiver claro, ",
    "ignore-o. ",
    "Antes de finalizar, descarte qualquer card que não seja fiel à fonte, ",
    "atômico, inequívoco, auto-contido e útil para recuperação ativa. ",
    "SAÍDA: Frente (cloze); Verso (explicação breve)."
);

#[derive(Debug)]
pub enum GenerationError<E> {
    InvalidInput,
    Creation(E),
    Provider(E),
    Cleanup { generation: Box<Self>, cleanup: E },
}

#[derive(Debug)]
pub struct GenerationResult<E> {
    pub deck: Deck,
    pub cleanup_error: Option<E>,
}

impl<E: Error> fmt::Display for GenerationError<E> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidInput => "Invalid document generation input",
            Self::Provider(_) => "NotebookLM document generation failed",
            Self::Creation(_) => "Temporary NotebookLM creation failed",
            Self::Cleanup { .. } => "Temporary NotebookLM cleanup could not be confirmed",
        })
    }
}
impl<E: Error + 'static> Error for GenerationError<E> {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Provider(error) | Self::Creation(error) => Some(error),
            Self::Cleanup { generation, .. } => Some(generation.as_ref()),
            Self::InvalidInput => None,
        }
    }
}

/// # Errors
/// Rejects invalid input and propagates notebook creation, generation, and cleanup failures.
pub async fn generate_document<F: TemporaryNotebookFactory>(
    factory: &F,
    path: &Path,
    name: &str,
    options: &GenerationOptions,
) -> Result<
    GenerationResult<<F::Gateway as NotebookLMGateway>::Error>,
    GenerationError<<F::Gateway as NotebookLMGateway>::Error>,
> {
    if !valid_input(path, name) {
        return Err(GenerationError::InvalidInput);
    }
    generate_named_document(factory, path, name, name, options, None).await
}

pub(crate) fn valid_input(path: &Path, name: &str) -> bool {
    !(name.trim().is_empty()
        || name.chars().count() > 1000
        || !matches!(
            path.extension()
                .and_then(|extension| extension.to_str())
                .map(str::to_ascii_lowercase)
                .as_deref(),
            Some("pdf" | "pptx")
        ))
}

pub(crate) async fn generate_named_document<F: TemporaryNotebookFactory>(
    factory: &F,
    path: &Path,
    title: &str,
    name: &str,
    options: &GenerationOptions,
    chunk: Option<(usize, usize)>,
) -> Result<
    GenerationResult<<F::Gateway as NotebookLMGateway>::Error>,
    GenerationError<<F::Gateway as NotebookLMGateway>::Error>,
> {
    let mut scope = factory
        .create_temporary_notebook(title)
        .await
        .map_err(GenerationError::Creation)?;
    let result = generate_in_notebook(
        scope.gateway(),
        &scope.notebook().id,
        path,
        name,
        options,
        chunk,
    )
    .await;
    let cleanup = scope.close().await;
    match result {
        Ok(deck) => Ok(GenerationResult {
            deck,
            cleanup_error: cleanup.err(),
        }),
        Err(generation) => match cleanup {
            Ok(()) => Err(generation),
            Err(cleanup) => Err(GenerationError::Cleanup {
                generation: Box::new(generation),
                cleanup,
            }),
        },
    }
}

async fn generate_in_notebook<G: NotebookLMGateway>(
    gateway: &G,
    notebook: &str,
    path: &Path,
    name: &str,
    options: &GenerationOptions,
    chunk: Option<(usize, usize)>,
) -> Result<Deck, GenerationError<G::Error>> {
    let timeout = Duration::from_secs(u64::from(options.timeout()));
    let source = gateway
        .add_file_source(notebook, path)
        .await
        .map_err(GenerationError::Provider)?;
    gateway
        .wait_for_source(notebook, &source, timeout)
        .await
        .map_err(GenerationError::Provider)?;
    let instructions = if options.instructions().is_empty() {
        DEFAULT_INSTRUCTIONS
    } else {
        options.instructions()
    };
    let contextual = chunk.map(|(index, total)| {
        format!("{instructions}\n\nCONTEXT: This is part {index} of {total} of the document.")
    });
    let instructions = contextual.as_deref().unwrap_or(instructions);
    let artifact = gateway
        .generate_flashcards(notebook, &[source], instructions, options)
        .await
        .map_err(GenerationError::Provider)?;
    gateway
        .wait_for_artifact(notebook, &artifact.id, timeout)
        .await
        .map_err(GenerationError::Provider)?;
    let cards = gateway
        .download_flashcards(notebook, &artifact.id)
        .await
        .map_err(GenerationError::Provider)?;
    let mut deck = converted_deck(notebook, name, cards, options.single_cloze());
    if chunk.is_none() {
        deck.deduplicate_standard();
    }
    Ok(deck)
}

#[must_use]
pub fn build_deck(notebook: &str, name: &str, cards: Vec<Flashcard>, single_cloze: bool) -> Deck {
    let mut deck = converted_deck(notebook, name, cards, single_cloze);
    deck.deduplicate_standard();
    deck
}

fn converted_deck(notebook: &str, name: &str, cards: Vec<Flashcard>, single_cloze: bool) -> Deck {
    let tag = name.to_lowercase().replace(' ', "_");
    let mut deck = Deck::new(name.to_owned());
    deck.description = format!("Deck de {name}");
    notebook.clone_into(&mut deck.notebook_id);
    deck.flashcards = cards
        .into_iter()
        .filter_map(|card| cloze::convert(&card, single_cloze))
        .map(|mut card| {
            card.tags.push(tag.clone());
            card
        })
        .collect();
    deck
}
