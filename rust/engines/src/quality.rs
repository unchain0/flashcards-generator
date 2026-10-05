use flashcards_domain::Flashcard;
mod casefold;
use lazy_regex::{Lazy, lazy_regex};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    sync::LazyLock,
};

const TRIVIAL_WORDS: &str = "is are the a an and or but in on at to for of with by from as it this that these those was were be been have has";
const SUBJECTIVE_WORDS: [&str; 10] = [
    "good",
    "bad",
    "better",
    "worse",
    "best",
    "worst",
    "important",
    "useful",
    "powerful",
    "obsolete",
];
const MAX_FEATURES: usize = 50;
const MAX_PAIRS: usize = 100_000;
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static TOKENS: Lazy<regex::Regex> = lazy_regex!(r"[\p{L}\p{N}_]{2,}");
static STOP_WORDS: LazyLock<HashSet<&str>> = LazyLock::new(|| {
    include_str!("quality_stopwords.txt")
        .split_whitespace()
        .collect()
});
static CASEFOLD: LazyLock<HashMap<char, &str>> =
    LazyLock::new(|| casefold::DATA.iter().copied().collect());

#[derive(Debug, PartialEq, Eq)]
pub struct QualityStats {
    pub trivial_removed: usize,
    pub similar_removed: usize,
    pub kept: usize,
    pub truncated: bool,
}

pub struct SimilarityAnalysis {
    pub pairs: Vec<(usize, usize, f64)>,
    pub truncated: bool,
}

fn whitespace(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

pub fn is_trivial(front: &str, back: &str) -> bool {
    let lower = front.to_lowercase();
    lower
        .split(whitespace)
        .filter(|word| {
            !word.is_empty()
                && !TRIVIAL_WORDS
                    .split_whitespace()
                    .any(|trivial| trivial == *word)
        })
        .count()
        < 3
        || back
            .split(whitespace)
            .filter(|word| !word.is_empty())
            .count()
            < 2
        || SUBJECTIVE_WORDS.iter().any(|word| lower.contains(word))
}

fn normalized_front(front: &str) -> String {
    let mut folded = String::new();
    for character in front.chars() {
        if let Some(value) = CASEFOLD.get(&character) {
            folded.push_str(value);
        } else {
            folded.push(character);
        }
    }
    folded
        .split(whitespace)
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn vectors(cards: &[Flashcard]) -> Vec<Vec<f64>> {
    let mut frequency = BTreeMap::<String, usize>::new();
    let counts = cards
        .iter()
        .map(|card| {
            let lower = card.front.to_lowercase();
            let mut counts = BTreeMap::<String, usize>::new();
            for token in TOKENS
                .find_iter(&lower)
                .map(|matched| matched.as_str())
                .filter(|word| !STOP_WORDS.contains(word))
            {
                *counts.entry(token.to_owned()).or_default() += 1;
                *frequency.entry(token.to_owned()).or_default() += 1;
            }
            counts
        })
        .collect::<Vec<_>>();
    let mut features = frequency.into_iter().collect::<Vec<_>>();
    features.sort_by_key(|(_, count)| std::cmp::Reverse(*count));
    features.truncate(MAX_FEATURES);
    features.sort_by(|left, right| left.0.cmp(&right.0));
    let idf = features
        .iter()
        .map(|(term, _)| {
            let documents = counts
                .iter()
                .filter(|counts| counts.contains_key(term))
                .count();
            ((1.0 + count_as_float(cards.len())) / (1.0 + count_as_float(documents))).ln() + 1.0
        })
        .collect::<Vec<_>>();
    counts
        .iter()
        .map(|counts| {
            let mut vector = features
                .iter()
                .zip(&idf)
                .map(|((term, _), idf)| count_as_float(*counts.get(term).unwrap_or(&0)) * idf)
                .collect::<Vec<_>>();
            normalize_vector(&mut vector);
            vector
        })
        .collect()
}

#[expect(
    clippy::cast_precision_loss,
    reason = "TF-IDF preserves the Python/NumPy conversion of integer counts to float64"
)]
fn count_as_float(count: usize) -> f64 {
    count as f64
}

fn normalize_vector(vector: &mut [f64]) {
    let length = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    if length == 0.0 {
        return;
    }
    for value in vector {
        *value /= length;
    }
}

#[must_use]
pub fn find_similar_cards(cards: &[Flashcard]) -> SimilarityAnalysis {
    analyze(cards, MAX_PAIRS)
}

fn analyze(cards: &[Flashcard], max_pairs: usize) -> SimilarityAnalysis {
    let mut pairs = BTreeMap::new();
    let mut truncated = false;
    let mut first = HashMap::new();
    for (index, card) in cards.iter().enumerate() {
        let front = normalized_front(&card.front);
        if let Some(&original) = first.get(&front) {
            pairs.insert((original, index), 1.0);
        } else {
            first.insert(front, index);
        }
        if pairs.len() > max_pairs {
            pairs.pop_last();
            truncated = true;
        }
    }
    let vectors = vectors(cards);
    if vectors.first().is_none_or(Vec::is_empty) {
        return SimilarityAnalysis {
            pairs: pairs
                .into_iter()
                .map(|((left, right), value)| (left, right, value))
                .collect(),
            truncated,
        };
    }
    // ponytail: fifty features per pair; use an inverted index if card counts outgrow this scan.
    for left in 0..cards.len().saturating_sub(1) {
        if truncated || pairs.len() >= max_pairs {
            truncated = true;
            break;
        }
        if similar_row(left, &vectors, &mut pairs, max_pairs) {
            truncated = true;
            break;
        }
    }
    SimilarityAnalysis {
        pairs: pairs
            .into_iter()
            .map(|((left, right), value)| (left, right, value))
            .collect(),
        truncated,
    }
}

fn similar_row(
    left: usize,
    vectors: &[Vec<f64>],
    pairs: &mut BTreeMap<(usize, usize), f64>,
    max_pairs: usize,
) -> bool {
    for right in left + 1..vectors.len() {
        let similarity = vectors[left]
            .iter()
            .zip(&vectors[right])
            .map(|(left, right)| left * right)
            .sum::<f64>();
        if similarity >= 0.85 {
            pairs.entry((left, right)).or_insert(similarity);
        }
        if pairs.len() >= max_pairs {
            return true;
        }
    }
    false
}

pub fn filter(cards: &mut Vec<Flashcard>) -> QualityStats {
    let original = cards.len();
    cards.retain(|card| !is_trivial(&card.front, &card.back));
    let trivial_removed = original - cards.len();
    let analysis = find_similar_cards(cards);
    let removed = analysis
        .pairs
        .iter()
        .map(|(_, right, _)| *right)
        .collect::<BTreeSet<_>>();
    let mut index = 0;
    cards.retain(|_| {
        let keep = !removed.contains(&index);
        index += 1;
        keep
    });
    QualityStats {
        trivial_removed,
        similar_removed: removed.len(),
        kept: cards.len(),
        truncated: analysis.truncated,
    }
}

#[cfg(test)]
mod tests;
