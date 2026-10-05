use flashcards_integrations::csv_merge::LocalCsvMerger;
use flashcards_services::csv_merge::{CsvMerger, DEFAULT_MERGED_FILENAME, MergeCsvRequest};
use serde::Deserialize;
use std::fs;

#[derive(Deserialize)]
struct Case {
    name: String,
    files: Vec<(String, String)>,
    recursive: bool,
    deduplicate: bool,
    output: Option<String>,
    rows_before: Option<usize>,
    rows_written: Option<usize>,
    duplicates_removed: Option<usize>,
}

#[test]
fn matches_python_merge_bytes_counts_ordering_and_lenient_csv_reading() {
    let cases: Vec<Case> =
        serde_json::from_str(include_str!("fixtures/python_csv_merge.json")).unwrap();
    assert_eq!(cases.len(), 28);
    for case in cases {
        let directory = tempfile::tempdir().unwrap();
        for (name, content) in case.files {
            let path = directory.path().join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, content).unwrap();
        }
        let output = directory.path().join(DEFAULT_MERGED_FILENAME);
        let original = fs::read(&output).ok();
        let request = MergeCsvRequest::new(
            directory.path().to_owned(),
            DEFAULT_MERGED_FILENAME.into(),
            case.recursive,
            case.deduplicate,
        )
        .unwrap();
        let result = LocalCsvMerger.merge(&request);
        if let Some(expected) = case.output {
            let result = result.unwrap_or_else(|error| panic!("{}: {error}", case.name));
            assert_eq!(
                fs::read_to_string(&output).unwrap(),
                expected,
                "{}",
                case.name
            );
            assert_eq!(Some(result.rows_before), case.rows_before, "{}", case.name);
            assert_eq!(
                Some(result.rows_written),
                case.rows_written,
                "{}",
                case.name
            );
            assert_eq!(
                Some(result.duplicates_removed),
                case.duplicates_removed,
                "{}",
                case.name
            );
        } else {
            assert!(result.is_err(), "{}", case.name);
            assert_eq!(fs::read(&output).ok(), original, "{}", case.name);
        }
    }
}
