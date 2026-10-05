use flashcards_integrations_shared::monitoring::{self, Service};
use sentry::{EventSamplingStrategy, TracesSamplingStrategy, protocol::Event};
use serde_json::{Value, json};
#[cfg(unix)]
use std::os::unix::ffi::OsStringExt;
use std::{ffi::OsString, io::Write, process::Stdio, time::Duration};
use tokio::{io::AsyncReadExt, process::Command};

const CHILD_MODE: &str = "FLASHCARDS_MONITORING_TEST_CHILD";
const RECORD_PREFIX: &str = "MONITORING ";
const OUTPUT_LIMIT: usize = 16 * 1024;

#[path = "tests/logging.rs"]
mod logging;

struct Case {
    variables: Vec<(&'static str, OsString)>,
    expected: Value,
}

impl Case {
    fn accepted(
        variables: &[(&'static str, &'static str)],
        project: &str,
        sample: f64,
        environment: &str,
    ) -> Self {
        Self {
            variables: variables
                .iter()
                .map(|(name, value)| (*name, (*value).into()))
                .collect(),
            expected: json!({
                "accepted":true, "project":project, "sample":sample,
                "environment":environment, "private_event_removed":true,
                "default_pii":false, "breadcrumbs":0, "sessions":false,
                "traces_disabled":true, "stacktraces":true,
                "disabled_validation_event":(sample == 0.0).then_some("00000000-0000-0000-0000-000000000000"),
                "release":concat!("flashcards-generator@", env!("CARGO_PKG_VERSION"))
            }),
        }
    }

    fn rejected(variables: &[(&'static str, &'static str)]) -> Self {
        Self {
            variables: variables
                .iter()
                .map(|(name, value)| (*name, (*value).into()))
                .collect(),
            expected: json!({
                "accepted":false, "message":"Invalid Sentry configuration", "has_cause":false
            }),
        }
    }
}

#[tokio::test]
async fn monitoring_configuration_preserves_privacy_and_respects_precedence() {
    if let Ok(service) = std::env::var(CHILD_MODE) {
        let report = service.strip_prefix("logging-").map_or_else(
            || configuration_report(selected_service(&service)),
            |service| logging::report(selected_service(service)),
        );
        writeln!(std::io::stdout(), "{RECORD_PREFIX}{report}").unwrap();
        return;
    }
    check_service("web", "FLASHCARDS_WEB_SENTRY_DSN", "4512186402078720").await;
    check_service(
        "companion",
        "FLASHCARDS_COMPANION_SENTRY_DSN",
        "4512186407256064",
    )
    .await;
}

fn selected_service(service: &str) -> Service {
    match service {
        "web" => Service::Web,
        "companion" => Service::Companion,
        _ => panic!("Invalid fixture service"),
    }
}

async fn check_service(service: &str, variable: &'static str, project: &str) {
    for case in cases(variable, project) {
        assert_eq!(child_report(service, &case.variables).await, case.expected);
    }
    #[cfg(unix)]
    for case in native_cases(variable, project) {
        assert_eq!(child_report(service, &case.variables).await, case.expected);
    }
    assert_eq!(
        child_report(
            &format!("logging-{service}"),
            &[
                ("SENTRY_DSN", "https://public@private.invalid/123".into()),
                ("FLASHCARDS_SENTRY_ENABLED", "false".into()),
                ("RUST_LOG", "trace".into()),
            ],
        )
        .await,
        json!({"levels":["warning", "error"], "service":selected_service(service).name()}),
    );
}

#[cfg(unix)]
fn native_cases(variable: &'static str, project: &str) -> Vec<Case> {
    let environment = if cfg!(debug_assertions) {
        "development"
    } else {
        "production"
    };
    let mut cases = Vec::new();
    for name in [
        variable,
        "SENTRY_DSN",
        "FLASHCARDS_SENTRY_ENABLED",
        "SENTRY_ENVIRONMENT",
    ] {
        let mut case = Case::rejected(&[]);
        case.variables = vec![
            ("SENTRY_DSN", "https://public@private.invalid/123".into()),
            ("FLASHCARDS_SENTRY_ENABLED", "false".into()),
            (
                name,
                OsString::from_vec(b"private-sentry-value-\xff".to_vec()),
            ),
        ];
        cases.push(case);
    }
    let mut override_case = Case::accepted(
        &[
            (variable, "https://public@private.invalid/456"),
            ("FLASHCARDS_SENTRY_ENABLED", "false"),
        ],
        "456",
        0.0,
        environment,
    );
    override_case.variables.push((
        "SENTRY_DSN",
        OsString::from_vec(b"private-unused-\xff".to_vec()),
    ));
    cases.push(override_case);
    let mut unrelated = Case::accepted(
        &[("FLASHCARDS_SENTRY_ENABLED", "false")],
        project,
        0.0,
        environment,
    );
    unrelated.variables.push((
        "UNRELATED_NATIVE_VALUE",
        OsString::from_vec(b"private-unused-\xff".to_vec()),
    ));
    let other = if variable == "FLASHCARDS_WEB_SENTRY_DSN" {
        "FLASHCARDS_COMPANION_SENTRY_DSN"
    } else {
        "FLASHCARDS_WEB_SENTRY_DSN"
    };
    unrelated
        .variables
        .push((other, OsString::from_vec(b"private-unused-\xff".to_vec())));
    cases.push(unrelated);
    cases
}

fn cases(variable: &'static str, project: &str) -> Vec<Case> {
    let environment = if cfg!(debug_assertions) {
        "development"
    } else {
        "production"
    };
    let global = "https://public@private.invalid/123";
    let specific = "https://public@private.invalid/456";
    vec![
        Case::accepted(&[], project, 1.0, environment),
        Case::accepted(
            &[
                ("SENTRY_DSN", global),
                ("FLASHCARDS_SENTRY_ENABLED", "false"),
            ],
            "123",
            0.0,
            environment,
        ),
        Case::accepted(
            &[
                ("SENTRY_DSN", global),
                (variable, specific),
                ("FLASHCARDS_SENTRY_ENABLED", "false"),
                ("SENTRY_ENVIRONMENT", "production"),
            ],
            "456",
            0.0,
            "production",
        ),
        Case::accepted(
            &[
                ("FLASHCARDS_SENTRY_ENABLED", "0"),
                ("SENTRY_ENVIRONMENT", "development"),
            ],
            project,
            0.0,
            "development",
        ),
        Case::accepted(
            &[
                ("FLASHCARDS_SENTRY_ENABLED", "true"),
                ("SENTRY_ENVIRONMENT", "sentry-validation"),
            ],
            project,
            1.0,
            "sentry-validation",
        ),
        Case::accepted(
            &[
                ("FLASHCARDS_SENTRY_ENABLED", "1"),
                ("SENTRY_ENVIRONMENT", "staging"),
            ],
            project,
            1.0,
            "staging",
        ),
        Case::rejected(&[("FLASHCARDS_SENTRY_ENABLED", "FALSE")]),
        Case::rejected(&[("SENTRY_ENVIRONMENT", "private-environment")]),
        Case::rejected(&[("SENTRY_DSN", "http://public@private.invalid/123")]),
        Case::rejected(&[("SENTRY_DSN", "private-invalid-url")]),
        Case::rejected(&[("SENTRY_DSN", "https://private.invalid/abc")]),
        Case::rejected(&[("SENTRY_DSN", global), (variable, "private-invalid-url")]),
    ]
}

fn configuration_report(service: Service) -> Value {
    let event: Event<'static> = serde_json::from_value(json!({
        "message":"private-document", "server_name":"private-machine",
        "user":{"email":"private@example.invalid"},
        "request":{"headers":{"Authorization":"private-session"}, "data":"private-password"},
        "extra":{"cards":"private-cards"}, "tags":{"user_id":"private-identity"}
    }))
    .unwrap();
    let guard = match monitoring::initialize(service) {
        Ok(guard) => guard,
        Err(error) => {
            return json!({
                "accepted":false, "message":error.to_string(),
                "has_cause":std::error::Error::source(&error).is_some()
            });
        }
    };
    let options = guard.options();
    let sample = match &options.event_sampling_strategy {
        EventSamplingStrategy::FixedRate(rate) => Some(*rate),
        _ => None,
    };
    let disabled_validation_event = (sample == Some(0.0)).then(monitoring::validation_event);
    let traces_disabled = matches!(
        &options.traces_sampling_strategy,
        TracesSamplingStrategy::Disabled
    ) || matches!(&options.traces_sampling_strategy, TracesSamplingStrategy::FixedRate(rate) if *rate == 0.0);
    let clean = options
        .before_send
        .as_ref()
        .and_then(|callback| callback(event));
    let private_event_removed = clean.is_some_and(|event| {
        event
            .tags
            .get("service")
            .is_some_and(|name| name == service.name())
            && serde_json::to_string(&event).is_ok_and(|value| !value.contains("private"))
    });
    json!({
        "accepted":true,
        "project":options.dsn.as_ref().map(|dsn| dsn.project_id().to_string()),
        "sample":sample, "environment":options.environment,
        "private_event_removed":private_event_removed,
        "default_pii":options.send_default_pii,
        "breadcrumbs":options.max_breadcrumbs,
        "sessions":options.auto_session_tracking,
        "traces_disabled":traces_disabled,
        "stacktraces":options.attach_stacktrace,
        "disabled_validation_event":disabled_validation_event,
        "release":options.release
    })
}

async fn child_report(service: &str, variables: &[(&str, OsString)]) -> Value {
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args([
            "--exact",
            "tests::monitoring_configuration_preserves_privacy_and_respects_precedence",
            "--nocapture",
        ])
        .env_clear()
        .env(CHILD_MODE, service)
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
    let mut child = command.spawn().unwrap();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let operation = async {
        let (stdout, stderr) = tokio::join!(capture(stdout), capture(stderr));
        assert!(child.wait().await.unwrap().success(), "{stderr}");
        let record = stdout
            .lines()
            .find_map(|line| line.strip_prefix(RECORD_PREFIX))
            .unwrap();
        serde_json::from_str(record).unwrap()
    };
    tokio::time::timeout(Duration::from_secs(10), operation)
        .await
        .unwrap()
}

async fn capture(stream: impl tokio::io::AsyncRead + Unpin) -> String {
    let mut bytes = Vec::new();
    stream
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_to_end(&mut bytes)
        .await
        .unwrap();
    assert!(bytes.len() <= OUTPUT_LIMIT);
    String::from_utf8(bytes).unwrap()
}
