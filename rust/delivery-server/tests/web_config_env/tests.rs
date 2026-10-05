#![cfg(unix)]

use flashcards_delivery_server::web_config::WebConfig;
use serde_json::{Value, json};
use std::{ffi::OsString, io::Write, os::unix::ffi::OsStringExt, process::Stdio, time::Duration};
use tokio::{io::AsyncReadExt, process::Command};

const CHILD_MODE: &str = "FLASHCARDS_CONFIG_TEST_CHILD";
const RECORD_PREFIX: &str = "CONFIG ";
const SESSION: &str = "test-session-secret-0123456789abcdef";
const LOOKUP: &str = "test-lookup-secret-0123456789abcdef";
const DATABASE: &str = "postgresql://local/flashcards";
const VARIABLES: [(&str, &str); 11] = [
    ("FLASHCARDS_DATABASE_URL", DATABASE),
    ("FLASHCARDS_SESSION_SECRET", SESSION),
    ("FLASHCARDS_AUTH_LOOKUP_SECRET", LOOKUP),
    ("FLASHCARDS_ENVIRONMENT", "production"),
    ("FLASHCARDS_AUTO_CREATE_SCHEMA", "false"),
    ("FLASHCARDS_HOST", "::1"),
    ("FLASHCARDS_PORT", "65535"),
    ("FLASHCARDS_SESSION_TTL_SECONDS", "300"),
    ("FLASHCARDS_CORS_ORIGINS", "[\"https://example.com\"]"),
    (
        "FLASHCARDS_BOOTSTRAP_PASSWORD",
        "private-bootstrap-password",
    ),
    ("FLASHCARDS_STATIC_DIR", "test-static"),
];

#[tokio::test]
async fn reads_only_web_configuration_and_rejects_invalid_unicode_without_disclosure() {
    if std::env::var_os(CHILD_MODE).is_some() {
        writeln!(
            std::io::stdout(),
            "{RECORD_PREFIX}{}",
            configuration_report()
        )
        .unwrap();
        return;
    }
    for (name, value) in [
        (
            OsString::from("UNRELATED_NATIVE_ENV"),
            OsString::from_vec(b"private-runtime-value-\xff".to_vec()),
        ),
        (
            OsString::from_vec(b"UNRELATED_NATIVE_KEY_\xff".to_vec()),
            OsString::from("private-runtime-value"),
        ),
    ] {
        assert_eq!(
            child_report(&VARIABLES[..3], &[(name, value)]).await,
            accepted(false)
        );
    }
    assert_eq!(child_report(&VARIABLES, &[]).await, accepted(true));
    for (name, _) in VARIABLES {
        let invalid = OsString::from_vec(b"private-configuration-value-\xff".to_vec());
        assert_eq!(
            child_report(&VARIABLES, &[(name.into(), invalid)]).await,
            rejected(name)
        );
    }
    for (missing, message) in [
        (
            "FLASHCARDS_DATABASE_URL",
            "FLASHCARDS_DATABASE_URL is required",
        ),
        ("FLASHCARDS_SESSION_SECRET", "FLASHCARDS_SESSION_SECRET"),
        (
            "FLASHCARDS_AUTH_LOOKUP_SECRET",
            "FLASHCARDS_AUTH_LOOKUP_SECRET",
        ),
    ] {
        let variables = VARIABLES
            .iter()
            .copied()
            .filter(|(name, _)| *name != missing)
            .collect::<Vec<_>>();
        assert_eq!(child_report(&variables, &[]).await, rejected(message));
    }
}

fn accepted(explicit: bool) -> Value {
    json!({
        "accepted":true, "production":explicit,
        "host":if explicit {"::1"} else {"127.0.0.1"},
        "port":if explicit {65535} else {8000},
        "ttl":if explicit {300} else {2_592_000},
        "static_dir":if explicit {"test-static"} else {"src/flashcards_generator/delivery/web/static/dist"},
        "cors":if explicit {vec!["https://example.com"]} else {vec![]},
        "session_matches":true, "lookup_matches":true, "database_matches":true,
        "bootstrap_matches":explicit, "bootstrap_absent":!explicit
    })
}

fn rejected(variable: &str) -> Value {
    json!({"accepted":false, "message":variable, "has_cause":false})
}

fn configuration_report() -> Value {
    match WebConfig::from_env() {
        Ok(config) => json!({
            "accepted":true, "production":config.production,
            "host":config.host.to_string(), "port":config.port,
            "ttl":config.session_ttl_seconds,
            "static_dir":config.static_dir.to_str(),
            "cors":config.cors_origins.iter().map(|origin| origin.to_str().unwrap()).collect::<Vec<_>>(),
            "session_matches":config.session_secret == SESSION,
            "lookup_matches":config.lookup_secret == LOOKUP,
            "database_matches":config.database_url == DATABASE,
            "bootstrap_matches":config.bootstrap_password.as_deref() == Some("private-bootstrap-password"),
            "bootstrap_absent":config.bootstrap_password.is_none()
        }),
        Err(error) => json!({
            "accepted":false, "message":error.to_string(),
            "has_cause":std::error::Error::source(&error).is_some()
        }),
    }
}

async fn child_report(variables: &[(&str, &str)], overrides: &[(OsString, OsString)]) -> Value {
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args([
            "--exact",
            "tests::reads_only_web_configuration_and_rejects_invalid_unicode_without_disclosure",
            "--nocapture",
        ])
        .env_clear()
        .env(CHILD_MODE, "true")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if let Some(profile) = std::env::var_os("LLVM_PROFILE_FILE") {
        command.env("LLVM_PROFILE_FILE", profile);
    }
    for (name, value) in variables {
        command.env(name, value);
    }
    for (name, value) in overrides {
        command.env(name, value);
    }
    let mut child = command.spawn().unwrap();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let operation = async {
        let (stdout, stderr) = tokio::join!(capture(stdout), capture(stderr));
        assert!(child.wait().await.unwrap().success(), "{stderr}");
        assert!(!stderr.contains("private"));
        let record = stdout
            .lines()
            .find_map(|line| line.strip_prefix(RECORD_PREFIX))
            .unwrap();
        assert!(!record.contains("private"));
        serde_json::from_str(record).unwrap()
    };
    tokio::time::timeout(Duration::from_secs(10), operation)
        .await
        .unwrap()
}

async fn capture(stream: impl tokio::io::AsyncRead + Unpin) -> String {
    const LIMIT: usize = 16 * 1024;
    let mut bytes = Vec::new();
    stream
        .take((LIMIT + 1) as u64)
        .read_to_end(&mut bytes)
        .await
        .unwrap();
    assert!(bytes.len() <= LIMIT);
    String::from_utf8(bytes).unwrap()
}
