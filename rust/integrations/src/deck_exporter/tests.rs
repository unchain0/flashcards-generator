use super::*;
use flashcards_domain::Flashcard;
use std::fs;

#[test]
fn atomically_replaces_csv_and_preserves_delimiters_unicode_and_math() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("deck.csv");
    fs::write(&path, "previous export").unwrap();
    let mut deck = Deck::new("study".into());
    deck.add_flashcard(Flashcard {
        front: "Qual é o valor de \"x, y\"?\nUse $x^2$.".into(),
        back: "café, \"aspas\" e\t $$E=mc^2$$".into(),
        ..Flashcard::default()
    });
    CsvDeckExporter.export_csv(&deck, &path).unwrap();
    let expected = "\"Qual é o valor de \"\"x, y\"\"?\nUse \\(x^2\\).\",\"café, \"\"aspas\"\" e\t \\[E=mc^2\\]\"\r\n";
    assert_eq!(fs::read_to_string(&path).unwrap(), expected);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_path(&path)
        .unwrap();
    let record = reader.records().next().unwrap().unwrap();
    assert_eq!(record.len(), 2);
    assert_eq!(&record[0], "Qual é o valor de \"x, y\"?\nUse \\(x^2\\).");
    assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
    CsvDeckExporter
        .export_csv(&Deck::new("empty".into()), &path)
        .unwrap();
    assert_eq!(fs::read(&path).unwrap(), b"");
}

#[test]
fn preserves_existing_data_and_cleans_temporary_files_when_commit_fails() {
    let directory = tempfile::tempdir().unwrap();
    let blocked = directory.path().join("blocked.csv");
    fs::create_dir(&blocked).unwrap();
    let original = blocked.join("original");
    fs::write(&original, "preserve").unwrap();
    assert!(
        CsvDeckExporter
            .export_csv(&Deck::new("study".into()), &blocked)
            .is_err()
    );
    assert_eq!(fs::read_to_string(&original).unwrap(), "preserve");
    assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
    assert!(
        CsvDeckExporter
            .export_csv(
                &Deck::new("study".into()),
                &directory.path().join("missing/deck.csv")
            )
            .is_err()
    );
    assert!(
        CsvDeckExporter
            .export_csv(&Deck::new("study".into()), Path::new("/"))
            .is_err()
    );
}
