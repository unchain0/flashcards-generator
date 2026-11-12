use super::{Card, ExtractError, Jobs, PdfToText, TextExtractor, heuristic_cards, render_csv};

#[test]
fn sentences_split_on_terminators_and_keep_abbreviations() {
    let text =
        "A célula é a unidade básica. O DNA fica no núcleo!\n\nEx.: mitocôndria é a usina? Sim.";
    let parts = super::sentences(text);
    assert_eq!(
        parts,
        vec![
            "A célula é a unidade básica.".to_owned(),
            "O DNA fica no núcleo!".to_owned(),
            "Ex.: mitocôndria é a usina?".to_owned(),
            "Sim.".to_owned(),
        ]
    );
}

#[test]
fn cards_mask_the_most_informative_term() {
    let cards =
        heuristic_cards("A mitocôndria produz ATP para a célula em 12 reações do ciclo completo.");
    assert_eq!(cards.len(), 1);
    assert!(cards[0].front.contains("{{c1::12}}"), "{}", cards[0].front);
    assert_eq!(cards[0].back, "");
}

#[test]
fn dates_win_over_other_candidates() {
    let cards = heuristic_cards("O tratado foi assinado em 12/03/1998 pela assembleia geral.");
    assert_eq!(cards.len(), 1);
    assert!(cards[0].front.contains("{{c1::12/03/1998}}"));
}

#[test]
fn proper_nouns_are_candidates_after_the_first_word() {
    let cards = heuristic_cards("Segundo a pesquisa, Brasília concentra os órgãos federais.");
    assert_eq!(cards.len(), 1);
    assert!(
        cards[0].front.contains("{{c1::Brasília}}"),
        "{}",
        cards[0].front
    );
}

#[test]
fn long_technical_words_are_candidates() {
    let cards =
        heuristic_cards("O processo de fotossíntese transforma energia luminosa em química.");
    assert_eq!(cards.len(), 1);
    assert_eq!(
        cards[0].front,
        "O processo de {{c1::fotossíntese}} transforma energia luminosa em química."
    );
}

#[test]
fn short_sentences_are_ignored() {
    assert_eq!(heuristic_cards("Curto demais.").len(), 0);
}

#[test]
fn long_sentences_are_ignored() {
    let long = format!("{} 42.", "palavra ".repeat(40));
    assert_eq!(heuristic_cards(&long).len(), 0);
}

#[test]
fn sentences_without_candidate_are_ignored() {
    assert_eq!(
        heuristic_cards("uma frase comum com poucas letras e sem termos raros aqui.").len(),
        0
    );
}

#[test]
fn sentences_already_holding_a_cloze_are_ignored() {
    assert_eq!(
        heuristic_cards("O {{c1::núcleo}} guarda o material genético da célula.").len(),
        0
    );
}

#[test]
fn duplicates_are_kept_once() {
    let sentence = "A mitocôndria produz energia para a célula em 12 etapas.";
    let cards = heuristic_cards(&format!("{sentence} {sentence}"));
    assert_eq!(cards.len(), 1);
}

#[test]
fn output_is_capped() {
    let text = (0..80)
        .map(|index| format!("A observação número {index} registra o fenômeno em detalhe."))
        .collect::<Vec<_>>()
        .join(" ");
    assert_eq!(heuristic_cards(&text).len(), super::MAX_CARDS);
}

#[test]
fn csv_is_quoted_with_crlf_and_escapes_quotes() {
    let cards = vec![
        Card {
            front: "A {{c1::célula}} é a unidade.".to_owned(),
            back: String::new(),
        },
        Card {
            front: "Ele disse \"oi\", {{c1::depois}} saiu.".to_owned(),
            back: String::new(),
        },
    ];
    let csv = String::from_utf8(render_csv(&cards)).unwrap();
    assert_eq!(
        csv,
        "\"A {{c1::célula}} é a unidade.\",\"\"\r\n\"Ele disse \"\"oi\"\", {{c1::depois}} saiu.\",\"\"\r\n"
    );
}

#[test]
fn jobs_store_and_return_finished_work() {
    let jobs = Jobs::default();
    let id = jobs.insert(b"a,b\r\n".to_vec(), 1);
    assert_eq!(jobs.get(&id), Some((b"a,b\r\n".to_vec(), 1)));
    assert!(jobs.get("missing").is_none());
}

#[test]
fn both_sources_are_heuristic_until_the_llm_is_wired() {
    for has_key in [true, false] {
        let cards = super::source_from_env(has_key)
            .cards("A mitocôndria produz ATP para a célula em 12 reações do ciclo completo.");
        assert_eq!(cards.len(), 1);
    }
}

#[test]
fn empty_document_is_rejected() {
    assert_eq!(PdfToText.extract(&[]), Err(ExtractError::Empty));
}

#[test]
fn unreadable_document_is_rejected() {
    assert_eq!(
        PdfToText.extract(b"not a pdf"),
        Err(ExtractError::Unavailable)
    );
}
