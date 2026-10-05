#![cfg(unix)]

use flashcards_integrations::process::{ProcessOutput, run_bounded};
use std::{ffi::OsString, os::unix::ffi::OsStringExt, time::Duration};
use tokio::process::Command;

#[tokio::test]
async fn invalid_companion_origins_fail_without_disclosing_the_configured_value() {
    for origin in [
        None,
        Some(OsString::from_vec(
            b"https://private-user:private-password@private-origin-\xff".to_vec(),
        )),
        Some(OsString::from(
            "https://private-user:private-password@private.example",
        )),
    ] {
        let output = launch(origin).await;
        assert_eq!(output.status.code(), Some(1));
        let diagnostic = String::from_utf8(output.stderr).unwrap();
        assert!(!diagnostic.contains("private"), "{diagnostic}");
        assert!(!diagnostic.contains("panicked"), "{diagnostic}");
        assert_eq!(String::from_utf8(output.stdout).unwrap(), "");
        assert_ne!(diagnostic, "");
    }
}

async fn launch(origin: Option<OsString>) -> ProcessOutput {
    launch_config(origin, &[], &[]).await
}

async fn launch_config(
    origin: Option<OsString>,
    variables: &[(&str, OsString)],
    args: &[&str],
) -> ProcessOutput {
    let mut command = Command::new(env!("CARGO_BIN_EXE_flashcards-companion"));
    command
        .env_clear()
        .env("FLASHCARDS_SENTRY_ENABLED", "false")
        .envs(variables.iter().map(|(key, value)| (*key, value)))
        .args(args);
    if let Some(origin) = origin {
        command.env("FLASHCARDS_COMPANION_WEB_ORIGIN", origin);
    }
    if let Some(profile) = std::env::var_os("LLVM_PROFILE_FILE") {
        command.env("LLVM_PROFILE_FILE", profile);
    }
    run_bounded(&mut command, Duration::from_secs(10), 8192)
        .await
        .unwrap()
}

#[tokio::test]
async fn rejects_legacy_arguments_without_exposing_their_contents() {
    let output = launch_config(None, &[], &["synthetic-private-argument"]).await;
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(output.stdout.len(), 0);
    let diagnostic = String::from_utf8(output.stderr).unwrap();
    assert!(diagnostic.contains("FLASHCARDS_COMPANION_WEB_ORIGIN"));
    assert!(!diagnostic.contains("synthetic-private-argument"));
    assert!(!diagnostic.contains("panicked"));
}

#[tokio::test]
async fn rejects_parent_directory_profile_locations_privately_and_preserves_existing_files() {
    let directory = tempfile::tempdir().unwrap();
    let home = directory.path().join("private-home");
    let xdg = directory.path().join("private-xdg");
    let explicit = directory.path().join("private-profile");
    std::fs::create_dir_all(home.join(".local/share")).unwrap();
    std::fs::create_dir(&xdg).unwrap();
    let files = [
        explicit.clone(),
        xdg.join("flashcards-generator"),
        home.join(".local/share/flashcards-generator"),
    ];
    for path in &files {
        std::fs::write(path, b"preserve existing local data").unwrap();
    }
    for variables in [
        vec![],
        vec![(
            "FLASHCARDS_COMPANION_DATA_DIR",
            explicit.join("../other").into_os_string(),
        )],
        vec![("XDG_DATA_HOME", xdg.join("../other").into_os_string())],
        vec![("HOME", home.join("../other").into_os_string())],
        vec![
            ("XDG_DATA_HOME", "relative-private-path".into()),
            ("HOME", home.join("../other").into_os_string()),
        ],
    ] {
        let output = launch_config(
            Some("https://private-origin.example".into()),
            &variables,
            &[],
        )
        .await;
        assert_eq!(output.status.code(), Some(1));
        assert_eq!(output.stdout.len(), 0);
        let diagnostic = String::from_utf8(output.stderr).unwrap();
        assert_ne!(diagnostic, "");
        assert!(!diagnostic.contains("private-"), "{diagnostic}");
        assert!(!diagnostic.contains("panicked"), "{diagnostic}");
    }
    for path in files {
        assert_eq!(
            std::fs::read(path).unwrap(),
            b"preserve existing local data"
        );
    }
}
