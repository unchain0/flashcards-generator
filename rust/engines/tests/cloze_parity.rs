use flashcards_domain::Flashcard;
use flashcards_engines::cloze::convert;
use serde::Deserialize;

#[derive(Deserialize)]
struct ExpectedCard {
    front: String,
    back: String,
    tags: Vec<String>,
    source: String,
}

#[derive(Deserialize)]
struct Case {
    front: String,
    back: String,
    single_cloze: bool,
    expected: Option<ExpectedCard>,
}

#[test]
fn matches_python_reference_for_existing_and_generated_clozes() {
    let cases: Vec<Case> = serde_json::from_str(include_str!("fixtures/python_cloze.json"))
        .expect("Python reference fixture is valid JSON");
    assert_eq!(cases.len(), 40);
    for case in cases {
        let card = Flashcard {
            front: case.front,
            back: case.back,
            tags: vec!["study".into()],
            source: "local.pdf".into(),
        };
        let expected = case.expected.map(|expected| Flashcard {
            front: expected.front,
            back: expected.back,
            tags: expected.tags,
            source: expected.source,
        });
        assert_eq!(
            convert(&card, case.single_cloze),
            expected,
            "front={:?}, back={:?}, single_cloze={}",
            card.front,
            card.back,
            case.single_cloze
        );
    }
}
