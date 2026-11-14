use super::{CLOZE, TRIVIAL};
use flashcards_domain::Flashcard;
use std::collections::BTreeMap;

/// Why a generated card cannot be used as an Anki cloze.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub enum RejectReason {
    MissingBlank,
    FragmentedFront,
    BlankTooLong,
    BlankFunctionWord,
    BlankPunctuation,
    ExtraRepeatsAnswer,
}

impl RejectReason {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::MissingBlank => "missing_blank",
            Self::FragmentedFront => "fragmented_front",
            Self::BlankTooLong => "blank_too_long",
            Self::BlankFunctionWord => "blank_function_word",
            Self::BlankPunctuation => "blank_punctuation",
            Self::ExtraRepeatsAnswer => "extra_repeats_answer",
        }
    }
}

/// Counts of rejected cards by reason, ready to be printed in the run summary.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct RejectStats {
    counts: BTreeMap<RejectReason, usize>,
}

impl RejectStats {
    pub fn record(&mut self, reason: RejectReason) {
        *self.counts.entry(reason).or_default() += 1;
    }

    #[must_use]
    pub fn total(&self) -> usize {
        self.counts.values().sum()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.counts.is_empty()
    }

    #[must_use]
    pub fn get(&self, reason: RejectReason) -> usize {
        self.counts.get(&reason).copied().unwrap_or_default()
    }

    pub fn iter(&self) -> impl Iterator<Item = (&'static str, usize)> + '_ {
        self.counts
            .iter()
            .map(|(reason, count)| (reason.as_str(), *count))
    }
}

/// Removes cards that would reach Anki broken and counts each reason.
pub fn filter(cards: &mut Vec<Flashcard>) -> RejectStats {
    let mut stats = RejectStats::default();
    cards.retain(|card| match validate(card) {
        Ok(()) => true,
        Err(reason) => {
            stats.record(reason);
            false
        }
    });
    stats
}

/// # Errors
/// Returns the first structural problem that makes the card unusable.
pub fn validate(card: &Flashcard) -> Result<(), RejectReason> {
    let blanks = blanks(&card.front);
    if blanks.is_empty() {
        return Err(RejectReason::MissingBlank);
    }
    if fragmented(&card.front) {
        return Err(RejectReason::FragmentedFront);
    }
    for blank in &blanks {
        check_blank(blank)?;
    }
    if repeats_answer(&blanks, &card.back) {
        return Err(RejectReason::ExtraRepeatsAnswer);
    }
    Ok(())
}

fn blanks(front: &str) -> Vec<String> {
    CLOZE
        .captures_iter(front)
        .map(|capture| capture[1].trim().to_owned())
        .collect()
}

/// A complete sentence starts with a capital letter and ends with punctuation.
fn fragmented(front: &str) -> bool {
    let plain = CLOZE.replace_all(front, "$1");
    let text = plain.trim();
    let starts_well = text
        .chars()
        .find(|character: &char| character.is_alphabetic())
        .is_some_and(char::is_uppercase);
    let ends_well = text
        .chars()
        .rev()
        .find(|character| !character.is_whitespace())
        .is_some_and(|character| ".!?…\"')".contains(character));
    !(starts_well && ends_well)
}

fn check_blank(blank: &str) -> Result<(), RejectReason> {
    let words = blank.split_whitespace().count();
    if words == 0 || words > 4 {
        return Err(RejectReason::BlankTooLong);
    }
    let edge = |character: char| character.is_ascii_punctuation() || "…".contains(character);
    if blank.chars().next().is_some_and(edge) || blank.chars().last().is_some_and(edge) {
        return Err(RejectReason::BlankPunctuation);
    }
    if words == 1 && is_function_word(blank) {
        return Err(RejectReason::BlankFunctionWord);
    }
    Ok(())
}

fn is_function_word(word: &str) -> bool {
    let lowered = word.to_lowercase();
    TRIVIAL.contains(&lowered.as_str())
}

/// An Extra field must add context, not hand the answer back.
fn repeats_answer(blanks: &[String], back: &str) -> bool {
    let extra = back.trim().to_lowercase();
    if extra.is_empty() {
        return false;
    }
    blanks
        .iter()
        .filter(|blank| !blank.is_empty())
        .any(|blank| extra.contains(&blank.to_lowercase()))
}

#[cfg(test)]
mod tests;
