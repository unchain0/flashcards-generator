use super::*;
mod cleanup;
mod credentials;
mod diagnostics;
pub(super) mod filesystem_races;
mod polling;
mod protocol_boundaries;
mod response_boundaries;
use axum::{
    Form, Router,
    extract::Query,
    http::HeaderMap,
    response::IntoResponse,
    routing::{get, post},
};
use std::{
    collections::BTreeMap,
    sync::{Arc, atomic::AtomicUsize},
};

pub(super) const STORAGE: &str = r#"{"authuser":2,"cookies":[{"name":"SID","value":"synthetic-session","domain":".google.com","path":"/","expires":-1},{"name":"API","value":"rpc-only","domain":"notebooklm.google.com","path":"/_/"},{"name":"ACCOUNT","value":"must-not-send","domain":"accounts.google.com","path":"/"},{"name":"OLD","value":"expired","domain":".google.com","path":"/","expires":1}]}"#;

pub(super) fn artifact_row(id: &str, status: u8) -> Value {
    json!([
        id,
        "Flashcards",
        4,
        null,
        status,
        null,
        null,
        null,
        null,
        [null, [1]]
    ])
}

pub(super) fn framed(method: &str, result: &Value) -> String {
    let frame = json!([["wrb.fr", method, result.to_string()]]).to_string();
    format!(")]}}'\n{}\n{frame}\n", frame.len())
}

#[tokio::test]
async fn accepts_an_empty_notebook_library_from_a_null_rpc_result() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/", get(bootstrap_fixture))
        .route(RPC_PATH, post(|| async { framed("wXbhsf", &Value::Null) }));
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = NotebookLMClient::connect(STORAGE, &origin).await.unwrap();
    assert_eq!(
        client.list_notebooks().await.unwrap(),
        Vec::<Notebook>::new()
    );
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

#[tokio::test]
async fn runs_native_notebook_lifecycle_over_http_with_browser_options_and_cookie_scope() {
    let polls = Arc::new(AtomicUsize::new(0));
    let source_polls = Arc::new(AtomicUsize::new(0));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/", get(bootstrap_fixture))
        .route(RPC_PATH, post(wire_fixture))
        .with_state((origin.clone(), polls.clone(), source_polls));
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let profile = tempfile::tempdir().unwrap();
    let storage = profile.path().join("storage_state.json");
    std::fs::write(&storage, STORAGE).unwrap();
    let saved = read_storage_file(&storage).await.unwrap();
    assert_eq!(saved, STORAGE);
    let client = NotebookLMClient::connect(&saved, &origin).await.unwrap();
    let notebook = check_lifecycle(&client).await;
    check_invalid_calls(&client, &notebook).await;
    client.delete_notebook(&notebook.id).await.unwrap();
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
    let error = client.list_notebooks().await.unwrap_err();
    assert!(matches!(error, NotebookLMError::Http(_)));
    assert!(!format!("{error:?}").contains("synthetic-sid"));
    assert_eq!(std::fs::read_to_string(&storage).unwrap(), STORAGE);
    check_profile_reads(&profile, &storage).await;
}

#[test]
fn decodes_google_interactive_card_keys_without_accepting_malformed_text() {
    let front = "A {{c1::membrana}} regula a entrada de substâncias.";
    let back = "Permeabilidade seletiva & transporte.";
    let data = json!({"flashcards":[{"f":front,"b":back}]}).to_string();
    let html = format!(
        "<div data-app-data=\"{}\"></div>",
        html_escape::encode_double_quoted_attribute(&data)
    );
    let cards = protocol::flashcards(&html).unwrap();
    assert_eq!(cards.len(), 1);
    assert_eq!(cards[0].front, front);
    assert_eq!(cards[0].back, back);
    let rich = json!({"flashcards":[{
        "f":{"flashcardContentBlock":[{"type":"text","content":front},{"type":"markdown","content":"$E=mc^2$"}]},
        "b":{"flashcardContentBlock":[{"type":"TEXT","content":back}]}
    }]}).to_string();
    let html = format!(
        "<div data-app-data=\"{}\"></div>",
        html_escape::encode_double_quoted_attribute(&rich)
    );
    let rich_cards = protocol::flashcards(&html).unwrap();
    assert_eq!(rich_cards[0].front, format!("{front}\n$E=mc^2$"));
    assert_eq!(rich_cards[0].back, back);
    for blocks in [
        json!([]),
        json!([{"type":"image","content":"private-data"}]),
        json!([{"type":"text","content":null}]),
        json!([{"content":"private-data"}]),
        json!(vec![json!({"type":"text","content":"private-data"}); 129]),
    ] {
        let data =
            json!({"flashcards":[{"f":{"flashcardContentBlock":blocks},"b":"answer"}]}).to_string();
        let html = format!(
            "<div data-app-data=\"{}\"></div>",
            html_escape::encode_double_quoted_attribute(&data)
        );
        let error = protocol::flashcards(&html).unwrap_err();
        assert!(!format!("{error:?}").contains("private-data"));
    }
    for card in [
        json!({"f":null,"b":"answer"}),
        json!({"f":" ","b":"answer"}),
        json!({"f":"question","b":[]}),
        json!({"f":"question"}),
    ] {
        let data = json!({"flashcards":[card]}).to_string();
        let html = format!(
            "<div data-app-data=\"{}\"></div>",
            html_escape::encode_double_quoted_attribute(&data)
        );
        assert!(protocol::flashcards(&html).is_err());
    }
}

#[test]
fn resolves_local_account_metadata_and_marks_routing_headers_sensitive() {
    for (fields, expected) in [
        (json!({}), "0"),
        (json!({"authuser":2}), "2"),
        (
            json!({"authuser":2,"notebooklm":{"account":{"authuser":3}}}),
            "3",
        ),
        (
            json!({"notebooklm":{"account":{"email":"  learner+study@example.test  ","authuser":3}}}),
            "learner+study@example.test",
        ),
        (
            json!({"notebooklm":{"account":{"email":"  ","authuser":3}}}),
            "3",
        ),
        (json!({"authuser":2,"notebooklm":{"account":null}}), "2"),
    ] {
        let mut state = fields;
        state["cookies"] = serde_json::from_str::<Value>(STORAGE).unwrap()["cookies"].clone();
        let credentials = protocol::Credentials::parse(&state.to_string()).unwrap();
        assert_eq!(credentials.authuser(), expected);
        let header = credentials.authuser_header().unwrap();
        assert_eq!(header, expected);
        assert!(header.is_sensitive());
    }
    for account in [
        json!({"authuser":-1}),
        json!({"authuser":true}),
        json!({"email":"private\r\ninjected"}),
        json!({"email":"private account@example.test"}),
        json!({"email":"x".repeat(321)}),
    ] {
        let state = json!({"cookies":[],"notebooklm":{"account":account}});
        let error = protocol::Credentials::parse(&state.to_string())
            .err()
            .unwrap();
        assert!(!format!("{error:?}").contains("private"));
    }
}

#[test]
fn accepts_live_frame_length_variations_only_with_valid_unique_method_envelopes() {
    let payload = json!([[
        "wrb.fr",
        "test",
        serde_json::to_string(&json!(["ação 🦀"])).unwrap()
    ]])
    .to_string();
    for count in [
        payload.len(),
        payload.encode_utf16().count(),
        payload.len() + 1,
    ] {
        assert_eq!(
            protocol::decode_rpc(&format!(")]}}'\n{count}\n{payload}\n"), "test").unwrap(),
            json!(["ação 🦀"])
        );
    }
    for raw in [
        ")]}'\n10",
        ")]}'\n10\n{broken",
        ")]}'\n10\n[[\"wrb.fr\",\"other\",\"[]\"]]",
    ] {
        assert!(protocol::decode_rpc(raw, "test").is_err());
    }
}

#[test]
fn transmits_all_quantity_and_difficulty_combinations_in_python_wire_order() {
    for (quantity, quantity_code) in [("fewer", 1), ("standard", 2), ("more", 3)] {
        for (difficulty, difficulty_code) in [("easy", 1), ("medium", 2), ("hard", 3)] {
            let options: GenerationOptions =
                serde_json::from_value(json!({"quantity":quantity, "difficulty":difficulty}))
                    .unwrap();
            let params =
                protocol::flashcard_params("notebook", &["source".to_owned()], "prompt", &options);
            assert_eq!(params[2][9][1][6], json!([quantity_code, difficulty_code]));
        }
    }
}

#[test]
fn rejects_invalid_credentials_rpc_envelopes_and_flashcards_without_revealing_cookies() {
    let credentials = protocol::Credentials::parse(STORAGE).unwrap();
    assert!(credentials.header("/").unwrap().is_sensitive());
    for state in [
        r#"{"cookies":"private-cookie-value"}"#,
        r#"{"cookies":[{"name":"SID","value":"injected; cookie","domain":".google.com"}]}"#,
        r#"{"cookies":[{"name":"SID","value":"private-cookie-value","domain":"google.com.evil"}]}"#,
    ] {
        let error = match protocol::Credentials::parse(state) {
            Ok(credentials) => credentials.header("/").unwrap_err(),
            Err(error) => error,
        };
        assert!(!format!("{error:?}").contains("private-cookie-value"));
    }
    assert!(protocol::Credentials::parse(&" ".repeat(4 * 1024 * 1024 + 1)).is_err());
    assert_eq!(
        protocol::page_token(r#"{"SNlM0e":"escaped\"token"}"#, "SNlM0e").unwrap(),
        "escaped\"token"
    );
    assert!(protocol::page_token("<html>login</html>", "SNlM0e").is_err());
    for bad in ["", "../id", "a/b", "a?x=y", "á", &"a".repeat(129)] {
        assert!(protocol::identifier(bad).is_err());
    }
    for raw in [
        "<html>login</html>",
        ")]}'\n100\n[]",
        "[[\"wrb.fr\",\"test\",true]]",
        "[[\"wrb.fr\",\"test\",\"{}\"],[\"wrb.fr\",\"test\",\"{}\"]]",
        "[[\"wrb.fr\",\"test\",null,null,null,[\"UserDisplayableError\"]]]",
    ] {
        assert!(protocol::decode_rpc(raw, "test").is_err());
    }
    assert_eq!(
        protocol::decode_rpc(&framed("test", &Value::Null), "test").unwrap(),
        Value::Null
    );
    for value in [
        json!({"flashcards":"bad"}),
        json!({"flashcards":[{"front":"", "back":"answer"}]}),
        json!({"flashcards":[{"front":2, "back":"answer"}]}),
        json!({"flashcards":[null]}),
        json!({"unknown":[]}),
    ] {
        let html = format!(
            "<div data-app-data=\"{}\"></div>",
            html_escape::encode_double_quoted_attribute(&value.to_string())
        );
        assert!(protocol::flashcards(&html).is_err());
    }
    assert!(protocol::flashcards("<div></div>").is_err());
    assert!(protocol::artifact(&artifact_row("id", 99)).is_err());
    for row in [
        json!([["source-id"], "title"]),
        json!([[["source-id"], "title"]]),
        json!([[[["source-id"], "title"]]]),
    ] {
        assert_eq!(protocol::source_identifier(&row).unwrap(), "source-id");
    }
}

type WireState = (String, Arc<AtomicUsize>, Arc<AtomicUsize>);

async fn bootstrap_fixture(
    headers: HeaderMap,
    Query(query): Query<BTreeMap<String, String>>,
) -> &'static str {
    assert_eq!(headers[header::COOKIE], "SID=synthetic-session");
    assert_eq!(query["authuser"], "2");
    r#"<script>window.WIZ_global_data={"SNlM0e":"synthetic-csrf","FdrFJe":"synthetic-sid","cfb2h":"live-build-from-page"};</script>"#
}

async fn wire_fixture(
    axum::extract::State((origin, counter, source_polls)): axum::extract::State<WireState>,
    headers: HeaderMap,
    Query(query): Query<BTreeMap<String, String>>,
    Form(form): Form<BTreeMap<String, String>>,
) -> axum::response::Response {
    assert_eq!(
        headers[header::COOKIE],
        "SID=synthetic-session; API=rpc-only"
    );
    assert_eq!(headers[header::ORIGIN], origin);
    assert_eq!(headers["x-goog-authuser"], "2");
    assert_eq!(query["f.sid"], "synthetic-sid");
    assert_eq!(query["bl"], "live-build-from-page");
    assert_eq!(query["authuser"], "2");
    assert_eq!(query["_reqid"].parse::<u64>().unwrap() % 100_000, 1);
    assert_eq!(form["at"], "synthetic-csrf");
    let request: Value = serde_json::from_str(&form["f.req"]).unwrap();
    let method = request[0][0][0].as_str().unwrap();
    assert_eq!(method, query["rpcids"]);
    let params: Value = serde_json::from_str(request[0][0][1].as_str().unwrap()).unwrap();
    let result = match method {
        "rLM1Ne" => source_wire(&params, &source_polls),
        "wXbhsf" => json!([[["Existing", [], "notebook-1"]]]),
        "CCqFvf" => create_wire(&params),
        "izAoDd" => text_source_wire(&params),
        "R7cb6c" => generation_wire(&params, &query),
        "gArtLc" => {
            let status = if counter.fetch_add(1, Ordering::SeqCst) == 0 {
                1
            } else {
                3
            };
            json!([[
                artifact_row("artifact-1", status),
                artifact_row("artifact-failed", 4)
            ]])
        }
        "v9rmvd" => {
            assert_eq!(params, json!(["artifact-1"]));
            let data = json!({"flashcards":[{"question":"Qual é o <conceito> & significado?", "answer":"Resposta \"á\""},{"q":"Q", "a":"A"}]}).to_string();
            let html = format!(
                "<div data-app-data=\"{}\"></div>",
                html_escape::encode_double_quoted_attribute(&data)
            );
            json!([[null, null, null, null, null, null, null, null, null, [html]]])
        }
        "WWINqb" => {
            assert_eq!(params, json!([["notebook-2"], [2]]));
            Value::Null
        }
        "refused" => {
            return (StatusCode::OK, json!([["er", "refused", 429]]).to_string()).into_response();
        }
        "wrong-method" => return (StatusCode::OK, framed("another", &Value::Null)).into_response(),
        "unauthorized" => return StatusCode::UNAUTHORIZED.into_response(),
        "redirect" => {
            return (StatusCode::TEMPORARY_REDIRECT, [(header::LOCATION, "/")]).into_response();
        }
        _ => panic!("unexpected RPC method"),
    };
    (StatusCode::OK, framed(method, &result)).into_response()
}

async fn check_lifecycle(client: &NotebookLMClient) -> Notebook {
    let notebooks = client.list_notebooks().await.unwrap();
    assert_eq!(
        notebooks,
        vec![Notebook {
            id: "notebook-1".to_owned(),
            title: "Existing".to_owned()
        }]
    );
    let notebook = client.create_notebook("Tópico").await.unwrap();
    let source = client
        .add_text_source(&notebook.id, "Fonte", "Texto em português")
        .await
        .unwrap();
    let options: GenerationOptions =
        serde_json::from_value(json!({"quantity":"more", "difficulty":"easy"})).unwrap();
    client
        .wait_for_source(&notebook.id, &source, Duration::from_secs(5))
        .await
        .unwrap();
    assert!(matches!(
        client
            .wait_for_source(&notebook.id, "failed-source", Duration::from_secs(1))
            .await,
        Err(NotebookLMError::SourceFailed)
    ));
    assert!(matches!(
        client
            .wait_for_source(&notebook.id, "missing-source", Duration::from_millis(30))
            .await,
        Err(NotebookLMError::Timeout(_))
    ));
    let artifact = client
        .generate_flashcards(&notebook.id, &[source], "Perguntas", &options)
        .await
        .unwrap();
    assert_eq!(artifact.status, ArtifactStatus::Processing);
    client
        .wait_for_artifact(&notebook.id, &artifact.id, Duration::from_secs(5))
        .await
        .unwrap();
    let cards = client
        .download_flashcards(&notebook.id, &artifact.id)
        .await
        .unwrap();
    assert_eq!(cards.len(), 2);
    assert_eq!(cards[0].front, "Qual é o <conceito> & significado?");
    assert_eq!(cards[0].back, "Resposta \"á\"");
    assert_eq!(cards[1].front, "Q");
    assert!(matches!(
        client
            .wait_for_artifact(&notebook.id, "artifact-failed", Duration::from_secs(1))
            .await,
        Err(NotebookLMError::GenerationFailed)
    ));
    assert!(matches!(
        client
            .wait_for_artifact(&notebook.id, "missing", Duration::from_millis(30))
            .await,
        Err(NotebookLMError::Timeout(_))
    ));
    notebook
}

async fn check_invalid_calls(client: &NotebookLMClient, notebook: &Notebook) {
    let options: GenerationOptions =
        serde_json::from_value(json!({"quantity":"more", "difficulty":"easy"})).unwrap();
    for title in [String::new(), "é".repeat(1001)] {
        assert!(matches!(
            client.create_notebook(&title).await,
            Err(NotebookLMError::InvalidInput)
        ));
        assert!(matches!(
            client.add_text_source(&notebook.id, &title, "text").await,
            Err(NotebookLMError::InvalidInput)
        ));
    }
    for text in [" ".to_owned(), "x".repeat(MAX_RESPONSE_BYTES + 1)] {
        assert!(matches!(
            client.add_text_source(&notebook.id, "title", &text).await,
            Err(NotebookLMError::InvalidInput)
        ));
    }
    assert!(
        client
            .add_text_source("../bad", "title", "text")
            .await
            .is_err()
    );
    for sources in [Vec::new(), vec!["valid-source".to_owned(); 301]] {
        assert!(matches!(
            client
                .generate_flashcards(&notebook.id, &sources, "prompt", &options)
                .await,
            Err(NotebookLMError::InvalidInput)
        ));
    }
    assert!(matches!(
        client
            .generate_flashcards(
                &notebook.id,
                &["valid-source".to_owned()],
                &"é".repeat(100_001),
                &options,
            )
            .await,
        Err(NotebookLMError::InvalidInput)
    ));
    for method in ["unauthorized", "redirect"] {
        assert!(matches!(
            client.rpc(method, &Value::Null, None, "en").await,
            Err(NotebookLMError::Authentication)
        ));
    }
    assert!(matches!(
        client.rpc("refused", &Value::Null, None, "en").await,
        Err(NotebookLMError::RpcFailure)
    ));
    assert!(matches!(
        client.rpc("wrong-method", &Value::Null, None, "en").await,
        Err(NotebookLMError::Schema(_))
    ));
}

async fn check_profile_reads(profile: &tempfile::TempDir, storage: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{PermissionsExt, symlink};
        assert_eq!(
            std::fs::metadata(storage).unwrap().permissions().mode() & 0o777,
            0o600
        );
        let link = profile.path().join("unsafe-link.json");
        symlink(storage, &link).unwrap();
        assert!(matches!(
            read_storage_file(&link).await,
            Err(NotebookLMError::Authentication)
        ));
    }
    assert!(matches!(
        read_storage_file(&profile.path().join("missing.json")).await,
        Err(NotebookLMError::Io(_))
    ));
    assert!(matches!(
        read_storage_file(profile.path()).await,
        Err(NotebookLMError::Authentication)
    ));
}

fn source_wire(params: &Value, source_polls: &AtomicUsize) -> Value {
    assert_eq!(
        *params,
        json!([
            "notebook-2",
            null,
            [
                2,
                null,
                null,
                [1, null, null, null, null, null, null, null, null, null, [1]]
            ],
            null,
            0
        ])
    );
    let status = if source_polls.fetch_add(1, Ordering::SeqCst) == 0 {
        5
    } else {
        2
    };
    json!([[
        "Tópico",
        [
            [["source-1"], "Fonte", null, [null, status]],
            [["failed-source"], "Broken", null, [null, 3]]
        ],
        "notebook-2"
    ]])
}

fn create_wire(params: &Value) -> Value {
    assert_eq!(
        *params,
        json!([
            "Tópico",
            null,
            null,
            [
                2,
                null,
                null,
                [1, null, null, null, null, null, null, null, null, null, [1]]
            ]
        ])
    );
    json!(["Tópico", [], "notebook-2"])
}

fn text_source_wire(params: &Value) -> Value {
    assert_eq!(
        *params,
        json!([
            [[
                null,
                ["Fonte", "Texto em português"],
                null,
                2,
                null,
                null,
                null,
                null,
                null,
                null,
                1
            ]],
            "notebook-2",
            [
                2,
                null,
                null,
                [1, null, null, null, null, null, null, null, null, null, [1]]
            ]
        ])
    );
    json!([[[["source-1"], "Fonte", null, [null, 2]]]])
}

fn generation_wire(params: &Value, query: &BTreeMap<String, String>) -> Value {
    assert_eq!(query["hl"], "pt_BR");
    assert_eq!(
        *params,
        json!([
            [
                2,
                null,
                null,
                [1, null, null, null, null, null, null, null, null, null, [1]],
                [[1, 4, 8, 2, 3, 6]]
            ],
            "notebook-2",
            [
                null,
                null,
                4,
                [[["source-1"]]],
                null,
                null,
                null,
                null,
                null,
                [null, [1, null, "Perguntas", null, null, null, [3, 1]]]
            ]
        ])
    );
    json!([artifact_row("artifact-1", 1)])
}
