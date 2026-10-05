use super::*;
use std::{collections::VecDeque, sync::Mutex};

type PollCase = (Value, Result<bool, &'static str>);
type PollState = Arc<(&'static str, Mutex<VecDeque<Value>>)>;

#[tokio::test]
async fn artifact_polling_distinguishes_empty_pending_ready_failed_and_malformed_responses() {
    let mut wrong_type = artifact_row("artifact-1", 3);
    wrong_type[2] = json!(2);
    let mut wrong_subtype = artifact_row("artifact-1", 3);
    wrong_subtype[9][1][0] = json!(2);
    let mut missing_id = artifact_row("artifact-1", 3);
    missing_id[0] = Value::Null;
    let mut missing_status = artifact_row("artifact-1", 3);
    missing_status[4] = Value::Null;
    check_polling(
        "gArtLc",
        vec![
            (Value::Null, Ok(false)),
            (json!([]), Ok(false)),
            (json!([[]]), Ok(false)),
            (json!([artifact_row("artifact-1", 1)]), Ok(false)),
            (json!([artifact_row("artifact-1", 2)]), Ok(false)),
            (json!([artifact_row("artifact-1", 3)]), Ok(true)),
            (
                json!([artifact_row("artifact-1", 4)]),
                Err("generation failed"),
            ),
            (json!([[artifact_row("artifact-1", 3)]]), Ok(true)),
            (
                json!([artifact_row("other", 4), artifact_row("artifact-1", 3)]),
                Ok(true),
            ),
            (json!([artifact_row("other", 3)]), Ok(false)),
            (json!([wrong_type, wrong_subtype]), Ok(false)),
            (
                json!({"private": "synthetic-private-provider"}),
                Err("artifact list"),
            ),
            (json!([missing_id]), Err("artifact identifier")),
            (
                json!([artifact_row("synthetic-private-provider/secret", 3)]),
                Err("invalid input"),
            ),
            (
                json!([artifact_row("artifact-1", 99)]),
                Err("artifact status"),
            ),
            (json!([missing_status]), Err("artifact status")),
        ],
    )
    .await;
}

#[tokio::test]
async fn source_polling_distinguishes_missing_pending_ready_failed_and_malformed_responses() {
    let source = |id: Value, status: Value| {
        json!([[
            "Notebook",
            [[id, "Source", null, [null, status]]],
            "notebook-1"
        ]])
    };
    check_polling(
        "rLM1Ne",
        vec![
            (json!([["Notebook", null, "notebook-1"]]), Ok(false)),
            (json!([["Notebook", [], "notebook-1"]]), Ok(false)),
            (source(json!(["other"]), json!(2)), Ok(false)),
            (source(json!(["source-1"]), json!(1)), Ok(false)),
            (source(json!(["source-1"]), json!(2)), Ok(true)),
            (source(json!(["source-1"]), json!(3)), Err("source failed")),
            (source(json!(["source-1"]), json!(99)), Ok(false)),
            (source(json!(["source-1"]), Value::Null), Ok(false)),
            (source(json!(["source-1"]), json!("unknown")), Ok(false)),
            (Value::Null, Err("notebook sources")),
            (json!([["Notebook", {}]]), Err("notebook sources")),
            (source(Value::Null, json!(2)), Err("source identifier")),
            (
                source(json!(["synthetic-private-provider/secret"]), json!(2)),
                Err("invalid input"),
            ),
        ],
    )
    .await;
}

async fn check_polling(method: &'static str, cases: Vec<PollCase>) {
    let state = Arc::new((
        method,
        Mutex::new(cases.iter().map(|case| case.0.clone()).collect()),
    ));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/", get(bootstrap_fixture))
        .route(RPC_PATH, post(poll_fixture))
        .with_state(state.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    for (payload, expected) in cases {
        let result = if method == "gArtLc" {
            client.artifact_is_ready("notebook-1", "artifact-1").await
        } else {
            client.source_is_ready("notebook-1", "source-1").await
        };
        if let Err(error) = &result {
            assert!(!format!("{error:?}").contains("synthetic-private-provider"));
            assert!(!error.to_string().contains("synthetic-private-provider"));
        }
        let actual = result.map_err(|error| match error {
            NotebookLMError::GenerationFailed => "generation failed",
            NotebookLMError::SourceFailed => "source failed",
            NotebookLMError::Schema(field) => field,
            NotebookLMError::InvalidInput => "invalid input",
            error => panic!("Unexpected polling error: {error:?}"),
        });
        assert_eq!(actual, expected, "Polling payload: {payload}");
    }
    assert!(state.1.lock().unwrap().is_empty());
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn poll_fixture(
    axum::extract::State(state): axum::extract::State<PollState>,
    headers: HeaderMap,
    Query(query): Query<BTreeMap<String, String>>,
    Form(form): Form<BTreeMap<String, String>>,
) -> String {
    assert_eq!(
        headers[header::COOKIE],
        "SID=synthetic-session; API=rpc-only"
    );
    assert_eq!(headers["x-goog-authuser"], "2");
    assert_eq!(query["rpcids"], state.0);
    assert_eq!(query["source-path"], "/notebook/notebook-1");
    assert_eq!(form["at"], "synthetic-csrf");
    let request: Value = serde_json::from_str(&form["f.req"]).unwrap();
    assert_eq!(request[0][0][0], state.0);
    let params: Value = serde_json::from_str(request[0][0][1].as_str().unwrap()).unwrap();
    let expected = if state.0 == "gArtLc" {
        json!([
            [2],
            "notebook-1",
            "NOT artifact.status = \"ARTIFACT_STATUS_SUGGESTED\""
        ])
    } else {
        json!(["notebook-1", null, protocol::client_options(false), null, 0])
    };
    assert_eq!(params, expected);
    let payload = state.1.lock().unwrap().pop_front().unwrap();
    framed(state.0, &payload)
}
