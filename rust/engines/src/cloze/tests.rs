use super::*;

fn card(front: &str, back: &str) -> Flashcard {
    Flashcard {
        front: front.into(),
        back: back.into(),
        tags: vec!["study".into()],
        source: "local.pdf".into(),
    }
}

#[test]
fn preserves_answers_and_rejects_trivial_clozes() {
    let result = convert(&card("Qual é a capital da França?", "Paris"), false).unwrap();
    assert_eq!(result.front, "capital da França {{c1::Paris}}");
    assert_eq!(result.back, "Paris");
    assert_eq!(result.tags, vec!["study"]);
    assert!(convert(&card("Qual é a resposta?", "the"), false).is_none());
    assert!(convert(&card("Texto {{c1::the}} vazio", ""), false).is_none());
    assert!(convert(&card("Texto {{c1::}} vazio", ""), false).is_none());
    assert!(convert(&card("Qual?", ""), false).is_none());
}

#[test]
fn retains_one_meaningful_cloze_and_converts_existing_math() {
    let front = "Teste {{c9::the}} {{c2::energia::conceito}} e {{c3::$E=mc^2$::equação}}";
    let result = convert(&card(front, "  resposta\n final "), true).unwrap();
    assert_eq!(
        result.front,
        r"Teste the {{c1::energia::conceito}} e \(E=mc^2\)"
    );
    assert_eq!(result.back, "resposta final");
    assert!(convert(&card("Texto {{c1::the}} {{c2::and}}", ""), true).is_none());
}

#[test]
fn splits_sentences_without_losing_punctuation_or_unicode() {
    assert_eq!(
        split_sentences("Água é líquida. Terra é sólida! Luz é onda? Sim."),
        vec!["Água é líquida.", "Terra é sólida!", "Luz é onda?", "Sim."]
    );
    assert_eq!(
        process_sentence("A structure gives purpose and function to cells"),
        "A {{c1::structure}} gives {{c2::purpose}} and {{c3::function}} to cells"
    );
    assert_eq!(
        process_sentence("água é uma substância"),
        "água é uma {{c1::substância}}"
    );
}

#[test]
fn preserves_reference_results_for_fallback_text_and_incomplete_question_prefixes() {
    for (front, back, multiple, single) in [
        (
            "Pergunta.",
            "the and or but. and the or but.",
            "{{c1::the and or but.}} {{c2::and the or but.}}",
            "{{c1::the and or but.}} and the or but.",
        ),
        (
            "Pergunta.",
            "(((((((((((((((( )))) ))))) ))))). (((((((((((( )))) )))) )))).",
            "{{c1::(((((((((((((((( )))) ))))) ))}}))). {{c2::(((((((((((( )))) )))) )))).}}",
            "{{c1::(((((((((((((((( )))) ))))) ))}}))). (((((((((((( )))) )))) )))).",
        ),
        ("Qual?", "ATP", "Qual? {{c1::ATP}}", "Qual? {{c1::ATP}}"),
        (
            "Nota sem pergunta.",
            "ATP",
            "Nota sem pergunta. {{c1::ATP}}",
            "Nota sem pergunta. {{c1::ATP}}",
        ),
        (
            "Pergunta.",
            "Substância is the and or but. átomos compõem estruturas celulares.",
            "{{c1::Substância is the and or but}}. {{c2::átomos compõem estruturas celulares}}.",
            "{{c1::Substância is the and or but}}. átomos compõem estruturas celulares.",
        ),
    ] {
        for (single_cloze, expected) in [(false, multiple), (true, single)] {
            let converted = convert(&card(front, back), single_cloze).unwrap();
            assert_eq!(converted.front, expected);
            assert_eq!(converted.back, back);
            assert_eq!(converted.tags, ["study"]);
            assert_eq!(converted.source, "");
        }
    }
    for single_cloze in [false, true] {
        assert!(convert(&card("{{c1::x}}", "resposta"), single_cloze).is_none());
    }
}

#[test]
fn sentence_candidates_and_keywords_obey_the_conversion_preconditions() {
    assert!(KEYWORDS.iter().all(|word| !TRIVIAL.contains(word)));
    assert!(TRIVIAL.iter().all(|word| word.chars().count() <= 8));
    for raw in TRIVIAL.iter().chain(KEYWORDS.iter()).flat_map(|text| {
        ["", " ", "\t\n\u{1c}"]
            .map(|prefix| format!("{prefix}{text} and the or but. átomos celulares."))
    }) {
        let answer = clean(&raw);
        for sentence in split_sentences(&answer)
            .into_iter()
            .filter(|sentence| sentence.trim().chars().count() > 10)
        {
            let important = extract_important(sentence);
            assert_ne!(important, "");
            assert!(!TRIVIAL.contains(&important.to_lowercase().as_str()));
        }
    }
}
