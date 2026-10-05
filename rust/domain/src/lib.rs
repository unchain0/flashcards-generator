#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Flashcard {
    pub front: String,
    pub back: String,
    pub tags: Vec<String>,
    pub source: String,
}

impl Flashcard {
    #[must_use]
    pub fn to_anki_format(&self) -> String {
        format!("{}\t{}\t{}", self.front, self.back, self.tags.join(" "))
    }

    #[must_use]
    pub fn normalized_front(&self) -> String {
        self.front
            .to_lowercase()
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Deck {
    pub name: String,
    pub description: String,
    pub flashcards: Vec<Flashcard>,
    pub notebook_id: String,
    pub created_at: SystemTime,
}

impl Deck {
    #[must_use]
    pub fn new(name: String) -> Self {
        Self {
            name,
            description: String::new(),
            flashcards: Vec::new(),
            notebook_id: String::new(),
            created_at: SystemTime::now(),
        }
    }

    #[must_use]
    pub fn total_cards(&self) -> usize {
        self.flashcards.len()
    }

    pub fn add_flashcard(&mut self, card: Flashcard) {
        self.flashcards.push(card);
    }

    /// # Errors
    /// Rejects non-finite thresholds and values outside the inclusive unit interval.
    pub fn deduplicate(
        &mut self,
        similarity_threshold: f64,
    ) -> Result<usize, InvalidSimilarityThreshold> {
        if !similarity_threshold.is_finite() || !(0.0..=1.0).contains(&similarity_threshold) {
            return Err(InvalidSimilarityThreshold);
        }
        Ok(self.deduplicate_valid(similarity_threshold))
    }

    pub fn deduplicate_standard(&mut self) -> usize {
        self.deduplicate_valid(0.85)
    }

    fn deduplicate_valid(&mut self, similarity_threshold: f64) -> usize {
        let original_count = self.flashcards.len();
        let mut fronts: Vec<String> = Vec::new();
        self.flashcards
            .retain(|card| retain_unique_front(card, &mut fronts, similarity_threshold));
        original_count - self.flashcards.len()
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidSimilarityThreshold;

impl fmt::Display for InvalidSimilarityThreshold {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("similarity_threshold must be finite and between 0 and 1")
    }
}

impl Error for InvalidSimilarityThreshold {}

fn retain_unique_front(card: &Flashcard, fronts: &mut Vec<String>, threshold: f64) -> bool {
    let normalized = card.normalized_front();
    if fronts
        .iter()
        .any(|front| matching_ratio(&normalized, front) >= threshold)
    {
        return false;
    }
    fronts.push(normalized);
    true
}

#[expect(
    clippy::cast_precision_loss,
    reason = "Similarity preserves Python float division, including length rounding above 2^53"
)]
fn matching_ratio(left: &str, right: &str) -> f64 {
    let left = left.chars().collect::<Vec<_>>();
    let right = right.chars().collect::<Vec<_>>();
    let total = left.len() + right.len();
    if total == 0 {
        return 1.0;
    }
    let mut indexes: HashMap<char, Vec<usize>> = HashMap::new();
    for (index, character) in right.iter().enumerate() {
        indexes.entry(*character).or_default().push(index);
    }
    // Python's SequenceMatcher excludes popular anchors in sequences of 200+ characters.
    if right.len() >= 200 {
        indexes.retain(|_, positions| positions.len() <= right.len() / 100 + 1);
    }
    let mut ranges = vec![(0, left.len(), 0, right.len())];
    let mut matches = 0;
    while let Some((left_start, left_end, right_start, right_end)) = ranges.pop() {
        let (i, j, length) = longest_match(
            &left,
            &right,
            &indexes,
            left_start..left_end,
            right_start..right_end,
        );
        if length == 0 {
            continue;
        }
        matches += length;
        if left_start < i && right_start < j {
            ranges.push((left_start, i, right_start, j));
        }
        if i + length < left_end && j + length < right_end {
            ranges.push((i + length, left_end, j + length, right_end));
        }
    }
    2.0 * matches as f64 / total as f64
}

fn longest_match(
    left: &[char],
    right: &[char],
    indexes: &HashMap<char, Vec<usize>>,
    left_range: std::ops::Range<usize>,
    right_range: std::ops::Range<usize>,
) -> (usize, usize, usize) {
    let (mut best_left, mut best_right, mut size) = (left_range.start, right_range.start, 0);
    let mut previous = HashMap::new();
    for i in left_range.clone() {
        let positions = indexes.get(&left[i]).map_or(&[][..], Vec::as_slice);
        let row = matching_row(i, positions, &previous, &right_range);
        if row.size > size {
            (best_left, best_right, size) = (row.left, row.right, row.size);
        }
        previous = row.lengths;
    }
    while best_left > left_range.start
        && best_right > right_range.start
        && left[best_left - 1] == right[best_right - 1]
    {
        best_left -= 1;
        best_right -= 1;
        size += 1;
    }
    while best_left + size < left_range.end
        && best_right + size < right_range.end
        && left[best_left + size] == right[best_right + size]
    {
        size += 1;
    }
    (best_left, best_right, size)
}

#[derive(Default)]
struct MatchingRow {
    lengths: HashMap<usize, usize>,
    left: usize,
    right: usize,
    size: usize,
}

fn matching_row(
    i: usize,
    positions: &[usize],
    previous: &HashMap<usize, usize>,
    range: &std::ops::Range<usize>,
) -> MatchingRow {
    let mut row = MatchingRow::default();
    for &j in positions {
        if j < range.start {
            continue;
        }
        if j >= range.end {
            break;
        }
        let length = j
            .checked_sub(1)
            .and_then(|j| previous.get(&j))
            .copied()
            .unwrap_or(0)
            + 1;
        row.lengths.insert(j, length);
        if length > row.size {
            (row.left, row.right, row.size) = (i + 1 - length, j + 1 - length, length);
        }
    }
    row
}

#[cfg(test)]
mod tests;
use std::{collections::HashMap, error::Error, fmt, time::SystemTime};
pub mod identity;
