use super::{Deck, Flashcard, matching_ratio};

#[test]
fn preserves_anki_fields_and_normalizes_unicode_whitespace() {
    let card = Flashcard {
        front: "  ÁGUA\n é\t essencial  ".into(),
        back: "H₂O".into(),
        tags: vec!["química".into(), "água".into()],
        source: "local.pdf".into(),
    };
    assert_eq!(card.normalized_front(), "água é essencial");
    assert_eq!(
        card.to_anki_format(),
        "  ÁGUA\n é\t essencial  \tH₂O\tquímica água"
    );
    assert_eq!(Flashcard::default().to_anki_format(), "\t\t");
}

#[test]
fn deduplicates_normalized_fronts_and_preserves_first_card() {
    let mut deck = Deck::new("study".into());
    for (front, back) in [
        ("ÁGUA  pura", "first"),
        ("água\npura", "duplicate"),
        ("terra seca", "different"),
    ] {
        deck.add_flashcard(Flashcard {
            front: front.into(),
            back: back.into(),
            ..Flashcard::default()
        });
    }
    assert_eq!(deck.total_cards(), 3);
    assert_eq!(deck.deduplicate(1.0), Ok(1));
    assert_eq!(deck.flashcards[0].back, "first");
    assert_eq!(deck.total_cards(), 2);
    for threshold in [f64::NAN, f64::INFINITY, -0.1, 1.1] {
        assert!(deck.deduplicate(threshold).is_err());
    }
    assert_eq!(deck.deduplicate(0.0), Ok(1));
    assert_eq!(Deck::new("empty".into()).deduplicate(0.85), Ok(0));
}

#[test]
fn matches_python_sequence_matcher_anchor_rules() {
    assert_eq!(matching_ratio("tide", "diet"), 0.25);
    assert_eq!(matching_ratio("diet", "tide"), 0.5);
    assert_eq!(matching_ratio("água pura", "agua pura"), 8.0 / 9.0);
    assert_eq!(matching_ratio("abc", "abcd"), 6.0 / 7.0);
    assert_eq!(matching_ratio("", ""), 1.0);
    assert_eq!(matching_ratio("", "abc"), 0.0);
    for (left, right, expected) in [
        ("abcXYZdef", "abcUVWdef", 2.0 / 3.0),
        ("abXYcdZWef", "ab12cd34ef", 0.6),
        ("abcaabc", "abcabca", 6.0 / 7.0),
        ("abaaba", "baabaa", 5.0 / 6.0),
        ("cabdab", "dababc", 0.5),
        ("left-middle-right", "left-other-right", 24.0 / 33.0),
    ] {
        assert_eq!(matching_ratio(left, right), expected, "{left} / {right}");
    }
    assert_eq!(
        matching_ratio(
            &format!("A{}C", "B".repeat(200)),
            &format!("X{}Y", "B".repeat(200))
        ),
        0.0
    );
    let anchored = format!("ab{}cd", "x".repeat(200));
    assert_eq!(matching_ratio(&anchored, &anchored), 1.0);
    assert_eq!(
        matching_ratio(
            &format!("b{}", "a".repeat(200)),
            &format!("{}b", "a".repeat(200))
        ),
        1.0 / 201.0
    );
    assert_eq!(
        matching_ratio(
            &format!("{}b", "a".repeat(200)),
            &format!("b{}", "a".repeat(200))
        ),
        1.0 / 201.0
    );
}

#[test]
fn extends_unique_anchors_through_popular_runs_without_matching_different_boundaries() {
    let middle = format!("{}b{}", "a".repeat(100), "a".repeat(100));
    assert_eq!(matching_ratio(&middle, &middle), 1.0);
    for (left, right, expected) in [
        (format!("x{middle}"), format!("y{middle}"), 201.0 / 202.0),
        (format!("{middle}x"), format!("{middle}y"), 201.0 / 202.0),
        (format!("x{middle}x"), format!("y{middle}y"), 201.0 / 203.0),
    ] {
        assert_eq!(matching_ratio(&left, &right), expected);
    }
}

#[test]
fn invalid_thresholds_preserve_the_deck_and_provide_a_stable_diagnostic() {
    let mut deck = Deck::new("study".into());
    deck.add_flashcard(Flashcard {
        front: "cell membrane function".into(),
        back: "original explanation".into(),
        ..Flashcard::default()
    });
    let original = deck.clone();
    for threshold in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -0.1, 1.1] {
        let error = deck.deduplicate(threshold).unwrap_err();
        assert_eq!(
            error.to_string(),
            "similarity_threshold must be finite and between 0 and 1"
        );
        assert_eq!(deck, original);
    }
}

#[test]
fn standard_deduplication_keeps_distinct_concepts_and_the_original_explanation() {
    let mut deck = Deck::new("study".into());
    for (front, back) in [
        ("cell membrane function", "first"),
        ("cell membrane functions", "duplicate"),
        ("mitochondrial energy", "different"),
    ] {
        deck.add_flashcard(Flashcard {
            front: front.into(),
            back: back.into(),
            ..Flashcard::default()
        });
    }
    assert_eq!(deck.deduplicate_standard(), 1);
    assert_eq!(deck.total_cards(), 2);
    assert_eq!(deck.flashcards[0].back, "first");
    assert_eq!(deck.flashcards[1].back, "different");
    assert_eq!(deck.deduplicate_standard(), 0);
}
