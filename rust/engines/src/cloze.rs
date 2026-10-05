use crate::math::convert_to_anki_math_format;
use flashcards_domain::Flashcard;
use lazy_regex::{Lazy, lazy_regex};
use regex::Captures;

#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static CLOZE: Lazy<regex::Regex> = lazy_regex!(r"\{\{c\d+::(.+?)\}\}");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static WHITESPACE: Lazy<regex::Regex> = lazy_regex!(r"[\s\x1c-\x1f]+");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static ELLIPSIS: Lazy<regex::Regex> = lazy_regex!(r"\.{3,}");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static ANSWER_PORTUGUESE: Lazy<regex::Regex> = lazy_regex!(r"(?i)\bResposta\s+[ée]/são\s*");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static ANSWER_ENGLISH: Lazy<regex::Regex> = lazy_regex!(r"(?i)\bAnswer\s+is/are\s*");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static QUESTION_PREFIX: Lazy<regex::Regex> =
    lazy_regex!(r"(?i)^(Qual é|Qual|What is|What|Which is|Which)\s*(o|a|the)?\s*");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static SENTENCE_BOUNDARY: Lazy<regex::Regex> = lazy_regex!(r"[.!?](\s+)");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static WORD_PUNCTUATION: Lazy<regex::Regex> = lazy_regex!(r"[,;:!?]$");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static DEFINITION: Lazy<regex::Regex> =
    lazy_regex!(r"([A-Z][a-z]+(?:\s+[a-z]+){0,4}\s+(?:é|são|is|are)\s+[^(,|.)]+)");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static PREDICATE: Lazy<regex::Regex> = lazy_regex!(r"((?:é|são|is|are)\s+[^(,|.)]+)");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static CONTENT: Lazy<regex::Regex> = lazy_regex!(r"([^(,|.)]{10,50})");

const TRIVIAL: &[&str] = &[
    "é", "são", "foi", "foram", "será", "serão", "is", "are", "was", "were", "will be", "o", "a",
    "os", "as", "the", "um", "uma", "uns", "umas", "an", "de", "da", "do", "das", "dos", "of",
    "em", "no", "na", "nos", "nas", "in", "on", "at", "e", "and", "ou", "or", "mas", "but", "que",
    "that", "which", "who", "com", "with", "sem", "without", "por", "para", "by", "for", "to",
    "se", "if", "whether", "como", "like", "mais", "maior", "more", "most", "menos", "menor",
    "less", "least", "muito", "pouco", "much", "many", "little", "few", "bem", "mal", "well",
    "badly", "já", "ainda", "yet", "still", "already", "também", "too", "also", "either", "só",
    "somente", "apenas", "only", "todos", "todas", "todo", "toda", "all", "every", "nenhum",
    "nenhuma", "none", "algum", "alguma", "alguns", "algumas", "some", "any", "esse", "essa",
    "esses", "essas", "this", "these", "those", "direita", "direito", "esquerda", "esquerdo",
    "right", "left",
];
const KEYWORDS: &[&str] = &[
    "definido como",
    "caracterizado por",
    "representa",
    "refere-se a",
    "denomina-se",
    "conhecido como",
    "principais",
    "função",
    "objetivo",
    "finalidade",
    "causa",
    "efeito",
    "consequência",
    "resultado",
    "processo",
    "mecanismo",
    "método",
    "técnica",
    "estrutura",
    "composição",
    "formado por",
    "localizado",
    "encontra-se",
    "situa-se",
    "responsável",
    "atua",
    "funciona",
    "diferença",
    "semelhança",
    "característica",
    "exemplo",
    "defined as",
    "characterized by",
    "represents",
    "refers to",
    "known as",
    "called",
    "main",
    "primary",
    "major",
    "function",
    "purpose",
    "goal",
    "cause",
    "effect",
    "result",
    "process",
    "mechanism",
    "method",
    "structure",
    "composed of",
    "located",
    "responsible",
    "acts",
    "works",
    "difference",
    "similarity",
    "feature",
];

#[must_use]
pub fn convert(card: &Flashcard, single_cloze: bool) -> Option<Flashcard> {
    let question = clean(&card.front);
    let answer = clean(&card.back);
    let existing = question.contains("{{c");
    let mut front = if existing {
        question
    } else {
        create_cloze(&question, &answer)
    };
    if single_cloze {
        front = limit_to_single_cloze(&front);
    }
    if existing {
        front = convert_to_anki_math_format(&front);
    }
    if !quality_valid(&front) {
        return None;
    }
    Some(Flashcard {
        front,
        back: if existing { answer } else { card.back.clone() },
        tags: card.tags.clone(),
        source: String::new(),
    })
}

fn clean(text: &str) -> String {
    let compact = WHITESPACE.replace_all(text, " ");
    let mut text = ELLIPSIS.replace_all(compact.trim(), "...").into_owned();
    for pattern in [&*ANSWER_PORTUGUESE, &*ANSWER_ENGLISH] {
        text = pattern.replace_all(&text, "").into_owned();
    }
    text
}

fn meaningful(text: &str) -> bool {
    text.to_lowercase()
        .split_whitespace()
        .any(|word| !TRIVIAL.contains(&word))
}

fn quality_valid(text: &str) -> bool {
    if text.chars().count() < 10 {
        return false;
    }
    let mut matches = CLOZE.captures_iter(text).peekable();
    matches.peek().is_some() && matches.all(|capture| meaningful(&capture[1]))
}

fn limit_to_single_cloze(text: &str) -> String {
    let mut retained = false;
    CLOZE
        .replace_all(text, |capture: &Captures<'_>| {
            let content = &capture[1];
            let answer = content
                .split_once("::")
                .map_or(content, |(answer, _)| answer);
            if !retained && meaningful(answer) {
                retained = true;
                cloze(content, 1)
            } else {
                answer.into()
            }
        })
        .into_owned()
}

fn cloze(text: &str, index: usize) -> String {
    format!("{{{{c{index}::{text}}}}}")
}

fn create_cloze(question: &str, answer: &str) -> String {
    if answer.split_whitespace().count() <= 3 {
        return short_answer_cloze(question, answer);
    }
    let sentences = split_sentences(answer);
    if sentences.len() == 1 {
        return process_sentence(answer);
    }
    sentences
        .into_iter()
        .take(3)
        .filter(|sentence| sentence.trim().chars().count() > 10)
        .enumerate()
        .map(|(index, sentence)| sentence_cloze(sentence, index + 1))
        .collect::<Vec<_>>()
        .join(" ")
}

fn short_answer_cloze(question: &str, answer: &str) -> String {
    if TRIVIAL.contains(&answer.trim().to_lowercase().as_str()) {
        return String::new();
    }
    if ["qual", "what", "which"]
        .iter()
        .any(|word| question.to_lowercase().contains(word))
    {
        let cleaned = QUESTION_PREFIX.replace(question, "");
        let cleaned = cleaned.trim_end_matches('?').trim();
        if !cleaned.is_empty() {
            return format!("{cleaned} {}", cloze(answer, 1));
        }
    }
    format!("{question} {}", cloze(answer, 1))
}

fn split_sentences(text: &str) -> Vec<&str> {
    let mut sentences = Vec::new();
    let mut start = 0;
    for boundary in SENTENCE_BOUNDARY.find_iter(text) {
        sentences.push(&text[start..=boundary.start()]);
        start = boundary.end();
    }
    sentences.push(&text[start..]);
    sentences
}

fn sentence_cloze(sentence: &str, index: usize) -> String {
    let important = extract_important(sentence);
    sentence.replacen(&important, &cloze(&important, index), 1)
}

fn extract_important(sentence: &str) -> String {
    for pattern in [&*DEFINITION, &*PREDICATE, &*CONTENT] {
        if let Some(capture) = pattern.captures(sentence)
            && meaningful(capture[1].trim())
        {
            let candidate = capture[1].trim();
            return candidate.into();
        }
    }
    sentence.chars().take(30).collect::<String>().trim().into()
}

fn process_sentence(sentence: &str) -> String {
    let words = sentence.split_whitespace().collect::<Vec<_>>();
    if words.len() <= 5 {
        return short_sentence_cloze(&words);
    }
    let mut count = 0;
    words
        .into_iter()
        .map(|word| keyword_cloze(word, &mut count))
        .collect::<Vec<_>>()
        .join(" ")
}

fn short_sentence_cloze(words: &[&str]) -> String {
    let index = words
        .iter()
        .enumerate()
        .find_map(|(index, word)| {
            let lower = word.to_lowercase();
            let clean = WORD_PUNCTUATION.replace(&lower, "");
            (!TRIVIAL.contains(&clean.as_ref())
                && (index > 0 || word.starts_with(char::is_uppercase)))
            .then_some(index)
        })
        .unwrap_or(words.len() / 2);
    words
        .iter()
        .enumerate()
        .map(|(position, word)| {
            if position == index && !TRIVIAL.contains(&word.to_lowercase().as_str()) {
                cloze(word, 1)
            } else {
                (*word).into()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn keyword_cloze(word: &str, count: &mut usize) -> String {
    let lower = word.to_lowercase();
    let clean = WORD_PUNCTUATION.replace(&lower, "");
    if *count < 3 && KEYWORDS.contains(&clean.as_ref()) {
        *count += 1;
        cloze(word, *count)
    } else {
        word.into()
    }
}

#[cfg(test)]
mod tests;
