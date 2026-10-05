use flashcards_domain::Flashcard;
use flashcards_services::generation::build_deck;

#[test]
fn deck_assembly_preserves_the_first_duplicate_and_applies_the_selected_cloze_policy() {
    let first = Flashcard {
        front: "{{c2::Mitocôndrias}} produzem {{c3::ATP}} para sustentar processos celulares."
            .into(),
        back: "A produção de energia sustenta o metabolismo celular.".into(),
        tags: vec!["organelas".into()],
        source: "provider-internal-source".into(),
    };
    let duplicate = Flashcard {
        back: "Esta duplicata deve ser descartada.".into(),
        ..first.clone()
    };
    let independent = Flashcard {
        front: "A {{c1::membrana plasmática}} regula a entrada de substâncias.".into(),
        back: "A permeabilidade é seletiva.".into(),
        ..Flashcard::default()
    };
    let trivial = Flashcard {
        front: "Texto com lacuna {{c1::the}} sem informação útil.".into(),
        ..Flashcard::default()
    };
    for (single_cloze, expected) in [
        (
            false,
            "{{c2::Mitocôndrias}} produzem {{c3::ATP}} para sustentar processos celulares.",
        ),
        (
            true,
            "{{c1::Mitocôndrias}} produzem ATP para sustentar processos celulares.",
        ),
    ] {
        let deck = build_deck(
            "notebook-1",
            "Biologia Celular",
            vec![
                first.clone(),
                duplicate.clone(),
                independent.clone(),
                trivial.clone(),
            ],
            single_cloze,
        );
        assert_eq!(deck.name, "Biologia Celular");
        assert_eq!(deck.description, "Deck de Biologia Celular");
        assert_eq!(deck.notebook_id, "notebook-1");
        assert_eq!(deck.total_cards(), 2);
        assert_eq!(deck.flashcards[0].front, expected);
        assert_eq!(deck.flashcards[0].back, first.back);
        assert_eq!(deck.flashcards[0].tags, ["organelas", "biologia_celular"]);
        assert_eq!(deck.flashcards[0].source, "");
        assert_eq!(deck.flashcards[1].front, independent.front);
        assert_eq!(deck.flashcards[1].tags, ["biologia_celular"]);
    }
}

#[test]
fn empty_decks_keep_their_identity_without_fabricating_cards() {
    let deck = build_deck("notebook-empty", "Biologia Celular", Vec::new(), false);
    assert_eq!(deck.notebook_id, "notebook-empty");
    assert_eq!(deck.name, "Biologia Celular");
    assert_eq!(deck.total_cards(), 0);
}
