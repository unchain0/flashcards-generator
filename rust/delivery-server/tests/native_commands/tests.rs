#![cfg(unix)]

use sqlx::{PgPool, postgres::PgConnectOptions};
use std::{
    ffi::OsString, os::unix::ffi::OsStringExt, process::Stdio, str::FromStr, time::Duration,
};
use tokio::{io::AsyncReadExt, process::Command};

#[tokio::test]
async fn native_arguments_and_provisioning_fail_without_disclosing_private_values() {
    let url =
        std::env::var("FLASHCARDS_TEST_DATABASE_URL").expect("disposable PostgreSQL URL required");
    let admin = PgPool::connect(&url).await.unwrap();
    let name = "flashcards_native_commands";
    sqlx::query("CREATE DATABASE flashcards_native_commands")
        .execute(&admin)
        .await
        .unwrap();
    let options = PgConnectOptions::from_str(&url).unwrap().database(name);
    let pool = PgPool::connect_with(options).await.unwrap();
    let url = format!("{}/{name}", url.rsplit_once('/').unwrap().0);
    let (code, _, stderr) = launch(&url, &["--migrate".into()], None).await;
    assert_eq!(code, Some(0), "{stderr}");
    for arguments in [
        vec![OsString::from_vec(b"private-argument-\xff".to_vec())],
        vec!["private-unknown-argument".into()],
        vec!["--help".into()],
        vec!["--migrate".into(), "private-extra-argument".into()],
    ] {
        let (code, stdout, stderr) = launch(&url, &arguments, None).await;
        assert_private_failure(code, &stdout, &stderr);
        assert!(stderr.contains("--provision-user"), "{stderr}");
    }
    for password in [
        Some(OsString::from_vec(b"private-password-\xff".to_vec())),
        None,
    ] {
        let (code, stdout, stderr) = launch(&url, &["--provision-user".into()], password).await;
        assert_private_failure(code, &stdout, &stderr);
        assert!(stderr.contains("FLASHCARDS_PROVISION_PASSWORD"), "{stderr}");
    }
    let users: i64 = sqlx::query_scalar("SELECT count(*) FROM web_users")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(users, 0);
    let (code, _, stderr) = launch(
        &url,
        &["--provision-user".into()],
        Some("native-test-password".into()),
    )
    .await;
    assert_eq!(code, Some(0), "{stderr}");
    let users: i64 = sqlx::query_scalar("SELECT count(*) FROM web_users")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(users, 1);
    pool.close().await;
    sqlx::query("DROP DATABASE flashcards_native_commands")
        .execute(&admin)
        .await
        .unwrap();
    admin.close().await;
}

fn assert_private_failure(code: Option<i32>, stdout: &str, stderr: &str) {
    assert_eq!(code, Some(1), "{stderr}");
    assert!(stdout.is_empty(), "{stdout}");
    assert!(!stderr.contains("private"), "{stderr}");
    assert!(!stderr.contains("panicked"), "{stderr}");
}

async fn launch(
    url: &str,
    arguments: &[OsString],
    password: Option<OsString>,
) -> (Option<i32>, String, String) {
    let mut command = Command::new(env!("CARGO_BIN_EXE_flashcards-web"));
    command
        .args(arguments)
        .env_clear()
        .env("FLASHCARDS_DATABASE_URL", url)
        .env(
            "FLASHCARDS_SESSION_SECRET",
            "native-session-secret-0123456789abcdef",
        )
        .env(
            "FLASHCARDS_AUTH_LOOKUP_SECRET",
            "native-lookup-secret-0123456789abcdef",
        )
        .env("FLASHCARDS_SENTRY_ENABLED", "false")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if let Some(password) = password {
        command.env("FLASHCARDS_PROVISION_PASSWORD", password);
    }
    if let Some(profile) = std::env::var_os("LLVM_PROFILE_FILE") {
        command.env("LLVM_PROFILE_FILE", profile);
    }
    let mut child = command.spawn().unwrap();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    tokio::time::timeout(Duration::from_secs(30), async {
        let (stdout, stderr) = tokio::join!(capture(stdout), capture(stderr));
        (child.wait().await.unwrap().code(), stdout, stderr)
    })
    .await
    .unwrap()
}

async fn capture(stream: impl tokio::io::AsyncRead + Unpin) -> String {
    let mut bytes = Vec::new();
    stream
        .take(16 * 1024 + 1)
        .read_to_end(&mut bytes)
        .await
        .unwrap();
    assert!(bytes.len() <= 16 * 1024);
    String::from_utf8(bytes).unwrap()
}
