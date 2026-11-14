use super::*;
use flashcards_domain::{Deck, Flashcard};
use flashcards_engines::quality;
use std::io;

#[test]
fn quality_and_cleanup_warnings_preserve_completed_exports_and_keep_error_causes() {
    let mut result = DocumentExportResult::<io::Error, io::Error, io::Error> {
        outcome: SourceOutcome::Completed,
        artifacts: vec!["biology.csv".into()],
        issues: Vec::new(),
        rejected: flashcards_engines::cloze::RejectStats::default(),
    };
    let mut deck = Deck::new("Biologia".into());
    deck.flashcards = (0..500)
        .map(|index| Flashcard {
            front: format!(
                "A {{{{c1::proteína}}}} transporta moléculas pela membrana celular {index}."
            ),
            back: "O transporte regula a entrada de substâncias na célula.".into(),
            ..Flashcard::default()
        })
        .collect();
    let stats = quality::filter(&mut deck.flashcards);
    assert!(stats.truncated);
    assert!(deck.total_cards() > 0);
    assert_eq!(stats.kept, deck.total_cards());
    let retained_deck = deck.clone();
    let mut generated = PreparedGenerationResult {
        deck,
        quality_stats: Some(stats),
        cleanup_errors: vec![io::Error::other("synthetic-private-notebook-id")],
        rejected: flashcards_engines::cloze::RejectStats::default(),
    };
    result.record_warnings(&mut generated);
    assert_eq!(result.outcome, SourceOutcome::Completed);
    assert_eq!(result.artifacts, ["biology.csv"]);
    assert_eq!(result.issues.len(), 2);
    assert!(matches!(result.issues[0], ExportIssue::QualityLimit));
    assert!(result.issues[0].source().is_none());
    assert!(matches!(result.issues[1], ExportIssue::Cleanup(_)));
    assert_eq!(
        result.issues[1].source().unwrap().to_string(),
        "synthetic-private-notebook-id"
    );
    assert!(
        !result.issues[1]
            .to_string()
            .contains("synthetic-private-notebook-id")
    );
    assert_eq!(generated.cleanup_errors.len(), 0);
    assert_eq!(generated.deck, retained_deck);
    generated.quality_stats = None;
    result.record_warnings(&mut generated);
    assert_eq!(result.issues.len(), 2);
    assert_eq!(result.outcome, SourceOutcome::Completed);
    assert_eq!(result.artifacts, ["biology.csv"]);
    assert_eq!(generated.deck, retained_deck);
}

#[test]
fn export_issue_diagnostics_preserve_typed_causes_without_disclosing_private_data() {
    let private = "synthetic-private-source-path";
    let issues = [
        ExportIssue::<io::Error, io::Error, io::Error>::Generation(
            PreparedGenerationError::Preparation(io::Error::other(private)),
        ),
        ExportIssue::Cleanup(io::Error::other(private)),
        ExportIssue::Export(io::Error::other(private)),
        ExportIssue::QualityLimit,
    ];
    for (index, issue) in issues.iter().enumerate() {
        assert_ne!(issue.to_string(), "");
        assert!(!issue.to_string().contains(private));
        assert_eq!(issue.source().is_some(), index != 3);
    }
    let preparation = issues[0].source().unwrap().source().unwrap();
    assert!(preparation.downcast_ref::<io::Error>().is_some());
    assert_eq!(preparation.to_string(), private);
    for issue in &issues[1..3] {
        let cause = issue.source().unwrap().downcast_ref::<io::Error>().unwrap();
        assert_eq!(cause.to_string(), private);
    }
}

#[test]
fn failed_document_removes_the_csv_files_written_before_the_failure() {
    let directory = tempfile::tempdir().unwrap();
    std::fs::write(directory.path().join("aula_chunk1.csv"), "front,back\n").unwrap();
    let mut result = DocumentExportResult::<io::Error, io::Error, io::Error> {
        outcome: SourceOutcome::Failed,
        artifacts: vec!["aula_chunk1.csv".into()],
        issues: Vec::new(),
        rejected: flashcards_engines::cloze::RejectStats::default(),
    };

    result.discard_artifacts(directory.path());

    assert_eq!(result.artifacts, Vec::<String>::new());
    assert!(!directory.path().join("aula_chunk1.csv").exists());
}

#[test]
fn rejected_cards_reach_the_export_summary_with_their_reasons() {
    let mut deck = Deck::new("Aula".into());
    deck.flashcards = vec![
        Flashcard {
            front: "A célula usa {{c1::glicose}} como combustível.".into(),
            back: String::new(),
            ..Flashcard::default()
        },
        Flashcard {
            front: "comando é utilizado no terminal{{c1::frequentemente}}.".into(),
            back: String::new(),
            ..Flashcard::default()
        },
    ];
    let rejected = flashcards_engines::cloze::filter_invalid(&mut deck.flashcards);
    let mut generated = PreparedGenerationResult {
        deck,
        quality_stats: None,
        cleanup_errors: Vec::new(),
        rejected,
    };
    let mut result = DocumentExportResult::<io::Error, io::Error, io::Error> {
        outcome: SourceOutcome::Completed,
        artifacts: Vec::new(),
        issues: Vec::new(),
        rejected: flashcards_engines::cloze::RejectStats::default(),
    };

    result.record_warnings(&mut generated);

    assert_eq!(result.rejected.total(), 1);
    assert_eq!(generated.deck.total_cards(), 1);
}

#[test]
fn chunk_export_drops_invalid_cards_without_touching_the_source_deck() {
    let mut deck = Deck::new("Aula".into());
    deck.flashcards = vec![
        Flashcard {
            front: "A célula usa {{c1::glicose}} como combustível.".into(),
            back: String::new(),
            ..Flashcard::default()
        },
        Flashcard {
            front: "O guia cita {{c1::exemplo,}} no capítulo.".into(),
            back: String::new(),
            ..Flashcard::default()
        },
    ];

    let clean = without_invalid_cards(&deck);

    assert_eq!(clean.total_cards(), 1);
    assert_eq!(deck.total_cards(), 2);
}
