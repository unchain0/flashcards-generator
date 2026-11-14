use super::{RejectReason, RejectStats, filter, validate};
use flashcards_domain::Flashcard;

fn card(front: &str, back: &str) -> Flashcard {
    Flashcard {
        front: front.to_owned(),
        back: back.to_owned(),
        ..Flashcard::default()
    }
}

fn reason(front: &str, back: &str) -> RejectReason {
    validate(&card(front, back)).expect_err("card should be rejected")
}

#[test]
fn accepts_a_complete_sentence_with_one_content_blank() {
    let valid = card(
        "A mitocôndria produz {{c1::energia}} para a célula.",
        "Capítulo 3: organelas celulares.",
    );
    assert_eq!(validate(&valid), Ok(()));
}

#[test]
fn rejects_a_front_that_starts_mid_sentence() {
    let rejected = card("comando é utilizado no terminal{{c1::frequentemente}}.", "");
    assert_eq!(
        reason(&rejected.front, &rejected.back),
        RejectReason::FragmentedFront
    );
}

#[test]
fn rejects_a_blank_over_a_function_word() {
    let rejected = card("O relatório foi enviado {{c1::para}} o setor.", "");
    assert_eq!(
        reason(&rejected.front, &rejected.back),
        RejectReason::BlankFunctionWord
    );
}

#[test]
fn rejects_a_blank_with_dangling_punctuation() {
    let rejected = card("O guia cita {{c1::exemplo,}} no capítulo.", "");
    assert_eq!(
        reason(&rejected.front, &rejected.back),
        RejectReason::BlankPunctuation
    );
}

#[test]
fn rejects_a_blank_longer_than_four_words() {
    let rejected = card(
        "O procedimento exige {{c1::uma lista completa de documentos assinados}} antes da entrega.",
        "",
    );
    assert_eq!(
        reason(&rejected.front, &rejected.back),
        RejectReason::BlankTooLong
    );
}

#[test]
fn rejects_an_extra_that_repeats_the_answer() {
    let rejected = card(
        "A membrana controla a {{c1::entrada}} de substâncias.",
        "A entrada é seletiva.",
    );
    assert_eq!(
        reason(&rejected.front, &rejected.back),
        RejectReason::ExtraRepeatsAnswer
    );
}

#[test]
fn rejects_a_front_without_any_blank() {
    let rejected = card("A célula possui organelas.", "");
    assert_eq!(
        reason(&rejected.front, &rejected.back),
        RejectReason::MissingBlank
    );
}

#[test]
fn filter_keeps_valid_cards_and_counts_each_reason() {
    let mut cards = vec![
        card("A célula usa {{c1::glicose}} como combustível.", ""),
        card("comando é utilizado no terminal{{c1::frequentemente}}.", ""),
        card("O relatório foi enviado {{c1::para}} o setor.", ""),
        card("O guia cita {{c1::exemplo,}} no capítulo.", ""),
    ];

    let stats = filter(&mut cards);

    assert_eq!(cards.len(), 1);
    assert_eq!(stats.total(), 3);
    assert_eq!(stats.get(RejectReason::FragmentedFront), 1);
    assert_eq!(stats.get(RejectReason::BlankFunctionWord), 1);
    assert_eq!(stats.get(RejectReason::BlankPunctuation), 1);
    assert!(!stats.is_empty());
    let printed: Vec<_> = stats.iter().collect();
    assert!(printed.contains(&("fragmented_front", 1)));
}

#[test]
fn empty_stats_report_zero() {
    let stats = RejectStats::default();
    assert_eq!(stats.total(), 0);
    assert!(stats.is_empty());
    assert_eq!(stats.get(RejectReason::MissingBlank), 0);
}
