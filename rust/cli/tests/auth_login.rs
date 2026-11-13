#![cfg(unix)]

use std::{fs, process::Command};

#[test]
fn login_help_lists_the_local_session_directory() {
    let output = Command::new(env!("CARGO_BIN_EXE_flashcards"))
        .args(["auth", "login", "--help"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("--data-dir"));
    assert!(stdout.contains("--browser"));
}

#[test]
fn login_without_a_browser_fails_before_writing_any_session() {
    let directory = tempfile::tempdir().unwrap();
    let data_dir = directory.path().join("session");
    let output = Command::new(env!("CARGO_BIN_EXE_flashcards"))
        .args(["auth", "login", "--data-dir"])
        .arg(&data_dir)
        .args(["--browser", "/does/not/exist/chromium"])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(stderr.contains("Chrome or Chromium is required"));
    assert!(!stderr.contains(data_dir.to_str().unwrap()));
    assert!(
        !data_dir
            .join("profiles")
            .join("00000000000000000000000000000000")
            .join("profiles/default/storage_state.json")
            .exists()
    );
}

#[test]
fn generation_requires_a_local_session_before_touching_sources() {
    let directory = tempfile::tempdir().unwrap();
    fs::write(directory.path().join("lesson.pdf"), []).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_flashcards"))
        .args(["generate", "--input-dir"])
        .arg(directory.path())
        .arg("--output-dir")
        .arg(directory.path().join("output"))
        .arg("--data-dir")
        .arg(directory.path().join("session"))
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .contains("NotebookLM login required")
    );
}
