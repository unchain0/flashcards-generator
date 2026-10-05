use super::quality;
use flashcards_domain::Flashcard;
use serde::Deserialize;

#[derive(Deserialize)]
struct Stats {
    trivial_removed: usize,
    similar_removed: usize,
    kept: usize,
}
#[derive(Deserialize)]
struct Case {
    cards: Vec<(String, String)>,
    trivial: Vec<bool>,
    pairs: Vec<(usize, usize, f64)>,
    kept: Vec<usize>,
    stats: Stats,
}

#[test]
fn bounds_public_similarity_pairs_for_duplicate_inputs_and_dense_scans() {
    const PAIR_LIMIT: usize = 100_000;
    let card = Flashcard {
        front: "membrane cellular transport molecules".into(),
        back: "Detailed answer".into(),
        tags: vec!["study".into()],
        source: "local.pdf".into(),
    };
    let cards = vec![card.clone(); PAIR_LIMIT + 2];
    for count in [PAIR_LIMIT + 1, PAIR_LIMIT + 2] {
        let analysis = quality::find_similar_cards(&cards[..count]);
        assert_eq!(analysis.pairs.len(), PAIR_LIMIT);
        assert!(analysis.truncated);
        assert!(analysis.pairs.iter().enumerate().all(|(offset, pair)| {
            pair.0 == 0 && pair.1 == offset + 1 && (pair.2 - 1.0).abs() < f64::EPSILON
        }));
        assert_eq!(cards[0], card);
        assert_eq!(cards[count - 1], card);
    }
    let dense = quality::find_similar_cards(&cards[..500]);
    assert_eq!(dense.pairs.len(), PAIR_LIMIT);
    assert!(dense.truncated);
    assert!(
        dense
            .pairs
            .iter()
            .all(|pair| { pair.0 < pair.1 && pair.1 < 500 && (pair.2 - 1.0).abs() < 1e-12 })
    );
    assert!(
        dense
            .pairs
            .windows(2)
            .all(|pair| { (pair[0].0, pair[0].1) < (pair[1].0, pair[1].1) })
    );
    assert_eq!(cards[499], card);
}

#[test]
fn matches_python_quality_filter_with_unicode_empty_vocabularies_and_large_feature_sets() {
    let cases: Vec<Case> =
        serde_json::from_str(include_str!("../../../tests/fixtures/python_quality.json")).unwrap();
    assert_eq!(cases.len(), 72);
    for (number, case) in cases.into_iter().enumerate() {
        let original = case
            .cards
            .iter()
            .enumerate()
            .map(|(index, (front, back))| Flashcard {
                front: front.clone(),
                back: back.clone(),
                tags: vec![format!("tag-{index}")],
                source: format!("source-{index}"),
            })
            .collect::<Vec<_>>();
        for (card, expected) in original.iter().zip(&case.trivial) {
            assert_eq!(
                quality::is_trivial(&card.front, &card.back),
                *expected,
                "trivial case {number}"
            );
        }
        let analysis = quality::find_similar_cards(&original);
        let coordinates = analysis
            .pairs
            .iter()
            .map(|(left, right, _)| (*left, *right))
            .collect::<Vec<_>>();
        let expected_coordinates = case
            .pairs
            .iter()
            .map(|(left, right, _)| (*left, *right))
            .collect::<Vec<_>>();
        assert_eq!(
            coordinates, expected_coordinates,
            "similarities case {number}"
        );
        for (actual, expected) in analysis.pairs.iter().zip(&case.pairs) {
            assert!((actual.2 - expected.2).abs() < 1e-12, "score case {number}");
        }
        let mut filtered = original.clone();
        let stats = quality::filter(&mut filtered);
        assert_eq!(
            stats.trivial_removed, case.stats.trivial_removed,
            "trivial count {number}"
        );
        assert_eq!(
            stats.similar_removed, case.stats.similar_removed,
            "similar count {number}"
        );
        assert_eq!(stats.kept, case.stats.kept, "retained count {number}");
        assert_eq!(
            filtered,
            case.kept
                .into_iter()
                .map(|index| original[index].clone())
                .collect::<Vec<_>>(),
            "retained cards {number}"
        );
        assert!(!stats.truncated);
    }
}
