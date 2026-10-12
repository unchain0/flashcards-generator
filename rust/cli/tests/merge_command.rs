use std::{fs, process::Command};

#[test]
fn merges_csv_files_through_the_installed_binary_contract() {
    let directory = tempfile::tempdir().unwrap();
    fs::write(directory.path().join("first.csv"), "front,back\n").unwrap();
    fs::write(directory.path().join("second.csv"), "other,answer\n").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_flashcards"))
        .args([
            "--json",
            "merge",
            "--folder",
            directory.path().to_str().unwrap(),
            "--output",
            "combined.csv",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        "{\"command\":\"merge\",\"duplicates_removed\":0,\"rows_before\":2,\"rows_written\":2}\n"
    );
    assert_eq!(
        fs::read_to_string(directory.path().join("combined.csv")).unwrap(),
        "\"front\",\"back\"\r\n\"other\",\"answer\"\r\n"
    );
}

#[test]
fn reports_invalid_input_on_stderr() {
    let output = Command::new(env!("CARGO_BIN_EXE_flashcards"))
        .args(["merge", "--folder", "/does/not/exist"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .starts_with("flashcards: ")
    );
}
