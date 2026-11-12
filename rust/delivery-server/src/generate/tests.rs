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

#[test]
fn leading_and_trailing_words_are_kept_when_the_sentence_has_no_terminator() {
    let parts = super::sentences("Primeira frase sem ponto. Segunda continua ate o fim");
    assert_eq!(
        parts,
        vec![
            "Primeira frase sem ponto.".to_owned(),
            "Segunda continua ate o fim".to_owned(),
        ]
    );
    assert_eq!(super::sentences("   ").len(), 0);
}

#[test]
fn numbers_with_hyphen_and_dates_with_bad_parts_are_not_dates() {
    assert!(!super::looks_like_date("12-abc-2020"));
    assert!(!super::looks_like_date("12345-01-01"));
    assert!(!super::looks_like_date("12/"));
    assert!(!super::looks_like_date("12/03"));
    assert!(super::looks_like_date("2020-03-12"));
}

#[test]
fn single_words_fill_the_gap_and_short_numbers_do_not() {
    let single = heuristic_cards("A observação do fenômeno natural ocorre isoladamente 12345.");
    assert_eq!(single.len(), 1);
    assert!(
        single[0].front.contains("{{c1::12345}}"),
        "{}",
        single[0].front
    );
    assert_eq!(
        heuristic_cards("O lote 7 tem 3 itens e cada um foi visto no dia.").len(),
        0
    );
}

#[test]
fn words_that_trim_to_nothing_are_skipped() {
    assert_eq!(super::weight("---", 1), 0);
    assert_eq!(super::weight("123", 1), 3);
}

#[test]
fn first_word_is_not_a_proper_noun_candidate() {
    assert_eq!(super::weight("Brasília", 0), 1);
    assert_eq!(super::weight("Brasília", 1), 2);
}

#[test]
fn abbreviation_and_date_helpers_reject_unknown_input() {
    assert!(super::ends_with_abbreviation("Ver fig."));
    assert!(!super::ends_with_abbreviation("Fim."));
    assert_eq!(super::candidate("x"), None);
    assert!(!super::is_sentence_end("fim.", 0));
    let _ = super::is_sentence_end("fim.", 3);
    assert_eq!(super::weight("...", 0), 0);
}

#[test]
fn jobs_recover_from_a_poisoned_lock() {
    let jobs = Jobs::default();
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _guard = jobs.finished.lock().unwrap();
        panic!("poison the job table");
    }));
    let id = jobs.insert(b"csv".to_vec(), 1);
    assert_eq!(jobs.get(&id), Some((b"csv".to_vec(), 1)));
}

fn fixture_dir(name: &str) -> std::path::PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("fc-{name}-{}-{id}", std::process::id()));
    std::fs::create_dir_all(&root).unwrap();
    root
}

fn script(path: &std::path::Path, body: &str) {
    use std::os::unix::fs::PermissionsExt;
    std::fs::write(path, body).unwrap();
    let mut permissions = std::fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o755);
    std::fs::set_permissions(path, permissions).unwrap();
}

#[test]
fn extractor_reports_a_missing_tool() {
    assert_eq!(
        super::extract_pdf_text("flashcards-missing-pdftotext", b"x"),
        Err(ExtractError::Unavailable)
    );
}

#[test]
fn extractor_reports_an_unsuccessful_process() {
    let directory = fixture_dir("exit");
    let program = directory.join("fail");
    script(&program, "#!/bin/sh\nexit 1\n");
    assert_eq!(
        super::extract_pdf_text(program.to_str().unwrap(), b"x"),
        Err(ExtractError::Unavailable)
    );
    std::fs::remove_dir_all(directory).unwrap();
}

#[test]
fn extractor_reports_empty_output() {
    let directory = fixture_dir("empty");
    let program = directory.join("empty");
    script(&program, "#!/bin/sh\ncat >/dev/null\nprintf '   \\n'\n");
    assert_eq!(
        super::extract_pdf_text(program.to_str().unwrap(), b"x"),
        Err(ExtractError::Unavailable)
    );
    std::fs::remove_dir_all(directory).unwrap();
}

#[test]
fn extractor_returns_the_process_output() {
    let directory = fixture_dir("ok");
    let program = directory.join("ok");
    script(
        &program,
        "#!/bin/sh\ncat >/dev/null\nprintf 'texto extraído'\n",
    );
    assert_eq!(
        super::extract_pdf_text(program.to_str().unwrap(), b"x"),
        Ok("texto extraído".to_owned())
    );
    std::fs::remove_dir_all(directory).unwrap();
}

struct Broken;

impl std::io::Write for Broken {
    fn write(&mut self, _buffer: &[u8]) -> std::io::Result<usize> {
        Err(std::io::Error::other("broken pipe"))
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[test]
fn extractor_input_and_output_helpers_report_failures() {
    assert_eq!(
        super::take_stdin(None).err(),
        Some(ExtractError::Unavailable)
    );
    assert_eq!(
        super::write_input(&mut Broken, b"x"),
        Err(ExtractError::Unavailable)
    );
    assert_eq!(
        super::output_text(Err(std::io::Error::other("wait failed"))),
        Err(ExtractError::Unavailable)
    );
}

#[test]
fn term_length_boundaries_are_enforced() {
    assert!(super::term_length_is_valid("12345", 2));
    assert!(!super::term_length_is_valid("1", 2));
    assert!(!super::term_length_is_valid(&"a".repeat(41), 3));
}

#[test]
fn llm_key_detection_requires_a_non_blank_value() {
    assert!(!super::key_present(None));
    assert!(!super::key_present(Some(String::new())));
    assert!(!super::key_present(Some("   ".to_owned())));
    assert!(super::key_present(Some("key".to_owned())));
}
