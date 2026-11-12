//! Server-side flashcard generation.
//!
//! v1 reads the PDF text on the server and builds cloze cards from rules, with
//! no external provider and no key. The generator sits behind [`CardSource`] so
//! an LLM implementation can replace the heuristic one through configuration
//! (`FLASHCARDS_LLM_API_KEY`) without touching the pipeline below it.

use std::{
    collections::HashMap,
    process::{Command, Stdio},
    sync::{
        Mutex,
        atomic::{AtomicU64, Ordering},
    },
};

/// Upper bound for one uploaded document.
pub const MAX_UPLOAD_BYTES: usize = 20 * 1024 * 1024;
const MAX_CARDS: usize = 40;

/// One generated card, already in the two-column layout the CSS exporter uses.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Card {
    /// Sentence with exactly one `{{c1::…}}` gap.
    pub front: String,
    /// Empty for cloze cards; kept for the two-column CSV the app exports.
    pub back: String,
}

/// Failure while reading a document.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExtractError {
    /// The upload is empty or unreadable.
    Empty,
    /// The extractor could not run or returned nothing usable.
    Unavailable,
}

/// Reads plain text out of a document.
pub trait TextExtractor: Send + Sync {
    /// # Errors
    /// Returns [`ExtractError`] when the document cannot be read.
    fn extract(&self, bytes: &[u8]) -> Result<String, ExtractError>;
}

/// Extracts text with `pdftotext`, the same tool family the companion ships.
pub struct PdfToText {
    program: String,
}

impl PdfToText {
    /// Uses a specific executable. Production uses [`Self::default`]; this is
    /// also useful for testing genuine subprocess failures without a mock.
    #[must_use]
    pub fn with_program(program: impl Into<String>) -> Self {
        Self {
            program: program.into(),
        }
    }
}

impl Default for PdfToText {
    fn default() -> Self {
        Self::with_program("pdftotext")
    }
}

impl TextExtractor for PdfToText {
    fn extract(&self, bytes: &[u8]) -> Result<String, ExtractError> {
        extract_pdf_text(&self.program, bytes)
    }
}

fn extract_pdf_text(program: &str, bytes: &[u8]) -> Result<String, ExtractError> {
    let mut command = Command::new(program);
    command
        .args(["-layout", "-", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    extract_with_command(&mut command, bytes)
}

fn extract_with_command(command: &mut Command, bytes: &[u8]) -> Result<String, ExtractError> {
    if bytes.is_empty() {
        return Err(ExtractError::Empty);
    }
    let mut child = command.spawn().map_err(|_| ExtractError::Unavailable)?;
    let mut stdin = take_stdin(child.stdin.take())?;
    write_input(&mut stdin, bytes)?;
    drop(stdin);
    output_text(child.wait_with_output())
}

fn take_stdin(
    stdin: Option<std::process::ChildStdin>,
) -> Result<std::process::ChildStdin, ExtractError> {
    stdin.ok_or(ExtractError::Unavailable)
}

fn write_input(writer: &mut impl std::io::Write, bytes: &[u8]) -> Result<(), ExtractError> {
    writer
        .write_all(bytes)
        .map_err(|_| ExtractError::Unavailable)
}

fn output_text(output: std::io::Result<std::process::Output>) -> Result<String, ExtractError> {
    let output = output.map_err(|_| ExtractError::Unavailable)?;
    if !output.status.success() {
        return Err(ExtractError::Unavailable);
    }
    let text = String::from_utf8_lossy(&output.stdout).into_owned();
    if text.trim().is_empty() {
        return Err(ExtractError::Unavailable);
    }
    Ok(text)
}

/// Turns extracted text into cloze cards.
pub trait CardSource: Send + Sync {
    fn cards(&self, text: &str) -> Vec<Card>;
}

/// Rule-based generator: no network, no key.
pub struct HeuristicSource;

impl CardSource for HeuristicSource {
    fn cards(&self, text: &str) -> Vec<Card> {
        heuristic_cards(text)
    }
}

/// Chooses the generator. The LLM implementation plugs in here; until the key
/// is configured and the provider is wired, the heuristic always runs.
#[must_use]
pub fn source_from_env(has_llm_key: bool) -> Box<dyn CardSource> {
    // shortcut: the LLM source lands with the key wiring; heuristic covers v1.
    let _ = has_llm_key;
    Box::new(HeuristicSource)
}

/// True when an LLM key is configured, so the log line can say what runs.
#[must_use]
pub fn llm_configured() -> bool {
    key_present(std::env::var("FLASHCARDS_LLM_API_KEY").ok())
}

fn key_present(value: Option<String>) -> bool {
    value.is_some_and(|value| !value.trim().is_empty())
}

/// Splits prose into sentences, keeping abbreviations from breaking words.
fn sentences(text: &str) -> Vec<String> {
    let flat = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let mut out = Vec::new();
    let mut start = 0;
    for (index, character) in flat.char_indices() {
        if !matches!(character, '.' | '!' | '?') {
            continue;
        }
        let candidate = &flat[start..=index];
        if is_sentence_end(&flat, index) && !ends_with_abbreviation(candidate) {
            out.push(candidate.trim().to_owned());
            start = index + character.len_utf8();
        }
    }
    out.push(flat[start..].trim().to_owned());
    out.into_iter()
        .filter(|sentence| !sentence.is_empty())
        .collect()
}

/// A terminator closes a sentence only when whitespace follows or the text ends.
fn is_sentence_end(flat: &str, index: usize) -> bool {
    flat[index + 1..]
        .chars()
        .next()
        .is_none_or(char::is_whitespace)
}

fn ends_with_abbreviation(sentence: &str) -> bool {
    let trimmed = sentence.trim_end_matches(['.', '!', '?']);
    let last = trimmed.split_whitespace().next_back().unwrap_or_default();
    matches!(
        last.to_ascii_lowercase().as_str(),
        "e.g" | "i.e" | "etc" | "dr" | "dra" | "prof" | "sr" | "sra" | "fig"
    )
}

/// Words that carry a fact worth asking about: numbers with units, dates,
/// proper nouns and long technical terms.
fn candidate(sentence: &str) -> Option<(usize, usize)> {
    let words = word_spans(sentence);
    let mut best: Option<(usize, usize, u8)> = None;
    for (index, (start, end)) in words.iter().copied().enumerate() {
        let word = &sentence[start..end];
        let current = weight(word, index);
        if current == 0 {
            continue;
        }
        let replace = match best {
            None => true,
            Some((old_start, old_end, old_weight)) => {
                (current, end - start) > (old_weight, old_end - old_start)
            }
        };
        if replace {
            best = Some((start, end, current));
        }
    }
    best.map(|(start, end, _)| (start, end))
}

fn weight(word: &str, index: usize) -> u8 {
    let trimmed = word.trim_matches(|character: char| {
        !character.is_alphanumeric() && character != '%' && character != '/'
    });
    if trimmed.is_empty() {
        return 0;
    }
    if looks_like_date(trimmed) {
        return 4;
    }
    let has_digit = trimmed.chars().any(|character| character.is_ascii_digit());
    if has_digit && trimmed.chars().any(char::is_alphabetic) {
        return 3;
    }
    if trimmed.chars().all(|character| character.is_ascii_digit()) && trimmed.len() > 1 {
        return 3;
    }
    let mut characters = trimmed.chars();
    let starts_upper = characters.next().is_some_and(char::is_uppercase);
    let proper = starts_upper
        && index > 0
        && trimmed.chars().any(char::is_lowercase)
        && trimmed.chars().count() > 2;
    if proper {
        return 2;
    }
    if trimmed.chars().count() >= 8 && trimmed.chars().all(char::is_alphabetic) {
        return 1;
    }
    0
}

fn looks_like_date(word: &str) -> bool {
    let parts: Vec<&str> = word.split(['/', '-']).collect();
    if parts.len() != 3 {
        return false;
    }
    parts.iter().all(|part| is_date_part(part))
}

fn is_date_part(part: &str) -> bool {
    part.len() <= 4 && !part.is_empty() && part.chars().all(|c| c.is_ascii_digit())
}

fn word_spans(sentence: &str) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    let mut start = None;
    for (index, character) in sentence.char_indices() {
        let is_word = character.is_alphanumeric() || matches!(character, '%' | '/' | '-');
        match (is_word, start) {
            (true, None) => start = Some(index),
            (false, Some(from)) => {
                spans.push((from, index));
                start = None;
            }
            _ => {}
        }
    }
    if let Some(from) = start {
        spans.push((from, sentence.len()));
    }
    spans
}

/// Builds dedupliced cloze cards, capped so one PDF cannot flood the export.
#[must_use]
pub fn heuristic_cards(text: &str) -> Vec<Card> {
    let mut cards: Vec<Card> = Vec::new();
    for sentence in sentences(text) {
        let Some(card) = card_for(&sentence) else {
            continue;
        };
        if !cards.iter().any(|existing| existing.front == card.front) {
            cards.push(card);
        }
        if cards.len() == MAX_CARDS {
            break;
        }
    }
    cards
}

/// Builds one card from a sentence, or `None` when it carries no good gap.
fn card_for(sentence: &str) -> Option<Card> {
    if sentence.chars().count() < 40 || sentence.chars().count() > 240 {
        return None;
    }
    if sentence.contains("{{c1::") {
        return None;
    }
    let (start, end) = candidate(sentence)?;
    let raw = &sentence[start..end];
    let leading = raw.len() - raw.trim_start().len();
    let trailing = raw.len() - raw.trim_end().len();
    let (start, end) = (start + leading, end - trailing);
    let term = &sentence[start..end];
    let shortest = if term.chars().all(|character| character.is_ascii_digit()) {
        2
    } else {
        3
    };
    if !term_length_is_valid(term, shortest) {
        return None;
    }
    let mut front = String::with_capacity(sentence.len() + term.len() + 8);
    front.push_str(&sentence[..start]);
    front.push_str("{{c1::");
    front.push_str(term);
    front.push_str("}}");
    front.push_str(&sentence[end..]);
    Some(Card {
        front: front.trim().to_owned(),
        back: String::new(),
    })
}

fn term_length_is_valid(term: &str, shortest: usize) -> bool {
    let length = term.chars().count();
    length >= shortest && length <= 40
}

/// Two quoted columns with CRLF, matching the deck exporter the app already
/// ships so Anki imports the file the same way.
#[must_use]
pub fn render_csv(cards: &[Card]) -> Vec<u8> {
    let mut out = String::new();
    for card in cards {
        out.push('"');
        out.push_str(&card.front.replace('"', "\"\""));
        out.push_str("\",\"");
        out.push_str(&card.back.replace('"', "\"\""));
        out.push_str("\"\r\n");
    }
    out.into_bytes()
}

/// A finished generation, kept in memory until the browser downloads it.
pub struct Finished {
    /// CSV bytes ready to download.
    pub csv: Vec<u8>,
    /// Number of cards inside the CSV.
    pub cards: usize,
}

/// In-memory job table for the upload → status → download flow.
#[derive(Default)]
pub struct Jobs {
    next: AtomicU64,
    finished: Mutex<HashMap<String, Finished>>,
}

impl Jobs {
    /// Stores a finished generation and returns its identifier.
    pub fn insert(&self, csv: Vec<u8>, cards: usize) -> String {
        let id = format!("{:016x}", self.next.fetch_add(1, Ordering::Relaxed));
        self.finished
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .insert(id.clone(), Finished { csv, cards });
        id
    }

    /// Returns the CSV and card count for an identifier.
    #[must_use]
    pub fn get(&self, id: &str) -> Option<(Vec<u8>, usize)> {
        self.finished
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(id)
            .map(|job| (job.csv.clone(), job.cards))
    }
}

#[cfg(test)]
mod tests;
