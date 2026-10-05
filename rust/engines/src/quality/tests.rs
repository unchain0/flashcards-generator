use super::*;
use crate::quality;

mod parity;

#[test]
fn compiled_casefold_table_preserves_every_reference_mapping() {
    let reference = include_str!("../quality_casefold.txt")
        .lines()
        .map(|line| {
            let (code, value) = line.split_once('\t').unwrap();
            let character = char::from_u32(u32::from_str_radix(code, 16).unwrap()).unwrap();
            (character, value)
        })
        .collect::<Vec<_>>();
    assert_eq!(casefold::DATA, reference);
    assert_eq!(CASEFOLD.len(), reference.len());
}

#[test]
fn bounds_similarity_pairs_even_when_all_terms_are_stop_words() {
    let cards = (0..20)
        .map(|index| Flashcard {
            front: format!("alpha beta gamma {index}"),
            back: "Detailed answer".into(),
            ..Flashcard::default()
        })
        .collect::<Vec<_>>();
    let analysis = analyze(&cards, 3);
    assert_eq!(analysis.pairs.len(), 3);
    assert!(analysis.truncated);
    assert_eq!(analyze(&cards, 0).pairs, Vec::<(usize, usize, f64)>::new());
    let mut cards = cards;
    for card in &mut cards {
        card.front = "could would should".into();
    }
    let analysis = analyze(&cards, 3);
    assert_eq!(analysis.pairs, [(0, 1, 1.0), (0, 2, 1.0), (0, 3, 1.0)]);
    assert!(analysis.truncated);
    assert_eq!(analyze(&cards, 0).pairs, Vec::<(usize, usize, f64)>::new());
    assert_eq!(vectors(&cards)[0], Vec::<f64>::new());
    assert_eq!(STOP_WORDS.len(), 318);
    assert_eq!(CASEFOLD.len(), 1557);
}

#[test]
fn repeated_nonempty_fronts_keep_the_earliest_bounded_pairs_without_mutating_cards() {
    let cards = (0..4)
        .map(|index| Flashcard {
            front: "Átomos formam estruturas celulares".into(),
            back: format!("Detailed answer {index}"),
            tags: vec![format!("tag-{index}")],
            source: format!("source-{index}.pdf"),
        })
        .collect::<Vec<_>>();
    let original = cards.clone();
    for (count, expected) in [(2, vec![(0, 1, 1.0)]), (4, vec![(0, 1, 1.0), (0, 2, 1.0)])] {
        let analysis = analyze(&cards[..count], expected.len());
        assert_eq!(analysis.pairs, expected);
        assert!(analysis.truncated);
        assert_eq!(cards, original);
    }
}
