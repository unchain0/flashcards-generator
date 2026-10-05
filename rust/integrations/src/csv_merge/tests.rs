use super::*;
use flashcards_services::csv_merge::DEFAULT_MERGED_FILENAME;
use std::io::{Seek, SeekFrom};

#[cfg(unix)]
#[test]
fn accepted_maximum_output_names_work_for_merging_and_deck_export() {
    use crate::deck_exporter::CsvDeckExporter;
    use flashcards_domain::{Deck, Flashcard};
    use flashcards_services::deck_exporter::DeckExporter;
    use std::os::unix::fs::PermissionsExt;

    for name in [
        format!("{}.csv", "a".repeat(251)),
        format!("{}x.csv", "é".repeat(125)),
    ] {
        assert_eq!(name.len(), 255);
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source.csv");
        let output = root.path().join(&name);
        fs::write(&source, "Front,Back\n").unwrap();
        fs::write(&output, "previous output").unwrap();
        let request = MergeCsvRequest::new(root.path().to_owned(), name, false, false).unwrap();
        let details = LocalCsvMerger.merge(&request).unwrap();
        assert_eq!(details.rows_written, 1);
        assert_eq!(
            fs::read_to_string(&output).unwrap(),
            "\"Front\",\"Back\"\r\n"
        );

        let mut deck = Deck::new("Study".into());
        deck.add_flashcard(Flashcard {
            front: "New front".into(),
            back: "New back".into(),
            ..Flashcard::default()
        });
        CsvDeckExporter.export_csv(&deck, &output).unwrap();
        assert_eq!(
            fs::read_to_string(&output).unwrap(),
            "\"New front\",\"New back\"\r\n"
        );
        assert_eq!(fs::read_to_string(&source).unwrap(), "Front,Back\n");
        assert_eq!(
            fs::metadata(&output).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 2);
    }
}

#[test]
fn merge_diagnostics_preserve_causes_without_disclosing_source_paths() {
    let csv_error = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_reader(b"front,\xff\n".as_slice())
        .records()
        .next()
        .unwrap()
        .unwrap_err();
    let errors = [
        MergeError::InvalidFolder,
        MergeError::NoSources,
        MergeError::UnsafeSource,
        MergeError::InvalidColumns,
        MergeError::Limit,
        MergeError::from(io::Error::other("synthetic-private-source-path")),
        MergeError::from(csv_error),
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), index >= 5);
        assert!(!error.to_string().contains("synthetic-private-source-path"));
    }
    assert_eq!(
        errors[5].source().unwrap().to_string(),
        "synthetic-private-source-path"
    );
}

#[test]
fn rejects_rows_after_the_limit_without_changing_written_rows_or_deduplication_state() {
    let output = tempfile::NamedTempFile::new().unwrap();
    let mut output = output.reopen().unwrap();
    let mut writer = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(&mut output);
    let record = csv::StringRecord::from(vec!["Front", "Back"]);
    let mut details = MergeDetails {
        rows_before: MAX_ROWS - 1,
        rows_written: MAX_ROWS - 1,
        duplicates_removed: 0,
    };
    let mut seen = HashSet::new();
    merge_record(&record, true, &mut writer, &mut details, &mut seen).unwrap();
    assert_eq!(details.rows_before, MAX_ROWS);
    assert_eq!(details.rows_written, MAX_ROWS);
    assert!(matches!(
        merge_record(&record, true, &mut writer, &mut details, &mut seen),
        Err(MergeError::Limit)
    ));
    assert_eq!(details.rows_before, MAX_ROWS);
    assert_eq!(details.rows_written, MAX_ROWS);
    assert_eq!(seen.len(), 1);
    writer.flush().unwrap();
    drop(writer);
    output.seek(SeekFrom::Start(0)).unwrap();
    let mut saved = String::new();
    output.read_to_string(&mut saved).unwrap();
    assert_eq!(saved, "Front,Back\n");
}

#[test]
fn enforces_aggregate_input_bytes_and_directory_entry_budgets_at_the_boundary() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("source.csv");
    fs::write(&source, b"A,B").unwrap();
    let mut bytes = MAX_DOCUMENT_BYTES - 3;
    assert_eq!(read_source(&source, &mut bytes).unwrap(), b"A,B");
    assert_eq!(bytes, MAX_DOCUMENT_BYTES);
    assert!(matches!(
        read_source(&source, &mut bytes),
        Err(MergeError::Limit)
    ));
    assert_eq!(bytes, MAX_DOCUMENT_BYTES);
    let mut folders = Vec::new();
    let mut files = Vec::new();
    let mut visited = MAX_ENTRIES - 1;
    let output = root.path().join(DEFAULT_MERGED_FILENAME);
    collect_folder(
        root.path(),
        &output,
        false,
        &mut folders,
        &mut files,
        &mut visited,
    )
    .unwrap();
    assert_eq!(visited, MAX_ENTRIES);
    assert_eq!(files, vec![source.clone()]);
    files.clear();
    assert!(matches!(
        collect_folder(
            root.path(),
            &output,
            false,
            &mut folders,
            &mut files,
            &mut visited
        ),
        Err(MergeError::Limit)
    ));
    assert_eq!(files, Vec::<PathBuf>::new());
    assert_eq!(fs::read(source).unwrap(), b"A,B");
    assert!(!output.exists());
}

fn request(root: &Path, recursive: bool, deduplicate: bool) -> MergeCsvRequest {
    MergeCsvRequest::new(
        root.to_owned(),
        DEFAULT_MERGED_FILENAME.into(),
        recursive,
        deduplicate,
    )
    .unwrap()
}
fn rows(path: &Path) -> Vec<Vec<String>> {
    csv::ReaderBuilder::new()
        .has_headers(false)
        .from_path(path)
        .unwrap()
        .records()
        .map(|row| row.unwrap().iter().map(str::to_owned).collect())
        .collect()
}

#[test]
fn preserves_sorted_sources_quoted_unicode_content_and_optional_pair_deduplication() {
    let root = tempfile::tempdir().unwrap();
    let nested = root.path().join("nested");
    fs::create_dir(&nested).unwrap();
    let a = "\" água, \"\"pura\"\"\ncontinua \",\" verso\t\"\r\nShort\n\n";
    let b = "\"água, \"\"pura\"\"\ncontinua\",\"verso\"\n\"água, \"\"pura\"\"\ncontinua\",\"outro verso\"\n";
    fs::write(root.path().join("a.csv"), a).unwrap();
    fs::write(root.path().join("b.csv"), b).unwrap();
    fs::write(nested.join("c.csv"), "Final,Resposta\n").unwrap();
    fs::write(root.path().join("ignored.CSV"), "Ignore,Case\n").unwrap();
    let output = root.path().join(DEFAULT_MERGED_FILENAME);
    fs::write(&output, "previous output").unwrap();
    let result = LocalCsvMerger
        .merge(&request(root.path(), true, true))
        .unwrap();
    assert_eq!(
        result,
        MergeDetails {
            rows_before: 4,
            rows_written: 3,
            duplicates_removed: 1
        }
    );
    assert_eq!(
        rows(&output),
        vec![
            vec![" água, \"pura\"\ncontinua ", " verso\t"],
            vec!["água, \"pura\"\ncontinua", "outro verso"],
            vec!["Final", "Resposta"]
        ]
    );
    assert_eq!(fs::read_to_string(root.path().join("a.csv")).unwrap(), a);
    assert_eq!(fs::read_to_string(root.path().join("b.csv")).unwrap(), b);
    let result = LocalCsvMerger
        .merge(&request(root.path(), false, false))
        .unwrap();
    assert_eq!(
        result,
        MergeDetails {
            rows_before: 3,
            rows_written: 3,
            duplicates_removed: 0
        }
    );
    assert_eq!(rows(&output).len(), 3);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&output).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}

#[test]
fn retains_bom_and_python_whitespace_comparison_without_normalizing_saved_fields() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("bom.csv"),
        "\u{feff}Front,Back\n\u{1c}Q\u{1f},\u{a0}A\u{2003}\nQ,A\n",
    )
    .unwrap();
    let result = LocalCsvMerger
        .merge(&request(root.path(), false, true))
        .unwrap();
    assert_eq!(
        result,
        MergeDetails {
            rows_before: 3,
            rows_written: 2,
            duplicates_removed: 1
        }
    );
    let expected = "\"\u{feff}Front\",\"Back\"\r\n\"\u{1c}Q\u{1f}\",\"\u{a0}A\u{2003}\"\r\n";
    assert_eq!(
        fs::read_to_string(root.path().join(DEFAULT_MERGED_FILENAME)).unwrap(),
        expected
    );
}

#[test]
fn preserves_previous_output_and_removes_temporaries_on_invalid_inputs_or_commit_failure() {
    let root = tempfile::tempdir().unwrap();
    let output = root.path().join(DEFAULT_MERGED_FILENAME);
    fs::write(&output, "preserve").unwrap();
    let source = root.path().join("source.csv");
    for malformed in [
        b"Valid,Back\nBad,Back,Extra\n".as_slice(),
        b"Front,\xff\n".as_slice(),
    ] {
        fs::write(&source, malformed).unwrap();
        assert!(
            LocalCsvMerger
                .merge(&request(root.path(), true, false))
                .is_err()
        );
        assert_eq!(fs::read(&output).unwrap(), b"preserve");
        assert_eq!(fs::read(&source).unwrap(), malformed);
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 2);
    }
    fs::remove_file(&output).unwrap();
    fs::create_dir(&output).unwrap();
    fs::write(output.join("original"), "preserve directory").unwrap();
    fs::write(&source, "Front,Back\n").unwrap();
    assert!(
        LocalCsvMerger
            .merge(&request(root.path(), false, false))
            .is_err()
    );
    assert_eq!(
        fs::read(output.join("original")).unwrap(),
        b"preserve directory"
    );
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 2);
    assert!(
        LocalCsvMerger
            .merge(&request(&root.path().join("missing"), false, false))
            .is_err()
    );
    assert!(matches!(
        LocalCsvMerger.merge(&request(&source, false, false)),
        Err(MergeError::InvalidFolder)
    ));
}

#[test]
fn handles_empty_and_short_sources_and_rejects_overlarge_files_without_publishing() {
    let root = tempfile::tempdir().unwrap();
    assert!(matches!(
        LocalCsvMerger.merge(&request(root.path(), true, false)),
        Err(MergeError::NoSources)
    ));
    let source = root.path().join("empty.csv");
    fs::write(&source, "short\n\n").unwrap();
    let selected =
        MergeCsvRequest::new(root.path().to_owned(), "custom.csv".into(), false, false).unwrap();
    assert_eq!(
        LocalCsvMerger.merge(&selected).unwrap(),
        MergeDetails::default()
    );
    let output = root.path().join("custom.csv");
    assert_eq!(fs::read(&output).unwrap(), b"");
    fs::write(&output, "preserve").unwrap();
    fs::File::options()
        .write(true)
        .open(&source)
        .unwrap()
        .set_len(MAX_DOCUMENT_BYTES + 1)
        .unwrap();
    assert!(matches!(
        LocalCsvMerger.merge(&selected),
        Err(MergeError::Limit)
    ));
    assert_eq!(fs::read(&output).unwrap(), b"preserve");
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 2);
}

#[cfg(unix)]
#[test]
fn refuses_symlink_sources_and_roots_without_reading_or_changing_external_files() {
    use std::os::unix::fs::symlink;
    let root = tempfile::tempdir().unwrap();
    let external = tempfile::tempdir().unwrap();
    let source = external.path().join("original.csv");
    fs::write(&source, "private outside source").unwrap();
    symlink(&source, root.path().join("linked.csv")).unwrap();
    assert!(matches!(
        LocalCsvMerger.merge(&request(root.path(), true, false)),
        Err(MergeError::UnsafeSource)
    ));
    assert!(!root.path().join(DEFAULT_MERGED_FILENAME).exists());
    let alias = root.path().join("alias");
    symlink(external.path(), &alias).unwrap();
    assert!(matches!(
        LocalCsvMerger.merge(&request(&alias, true, false)),
        Err(MergeError::InvalidFolder)
    ));
    assert_eq!(
        fs::read_to_string(&source).unwrap(),
        "private outside source"
    );
}
