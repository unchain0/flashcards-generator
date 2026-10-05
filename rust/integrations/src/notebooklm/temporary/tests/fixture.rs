use super::*;
use axum::{extract::State, response::Response};
pub(super) const CUSTOM_INSTRUCTIONS: &str =
    "Explain transport across the cell membrane using cloze cards.";

#[derive(Clone)]
pub(super) struct FixtureState {
    pub sequence: Arc<AtomicUsize>,
    pub mode: Arc<AtomicUsize>,
    pub attempts: Arc<AtomicUsize>,
    pub titles: Arc<Mutex<BTreeMap<String, String>>>,
    pub deleted: Arc<Mutex<Vec<String>>>,
    pub deleted_changed: Arc<tokio::sync::Notify>,
    pub registered: Arc<Mutex<BTreeSet<String>>>,
    pub blocked: Arc<Mutex<Option<String>>>,
    pub pending_seen: Arc<tokio::sync::Notify>,
    pub calls: Arc<Mutex<Vec<String>>>,
    pub origin: String,
}

pub(super) fn state(origin: &str) -> FixtureState {
    FixtureState {
        sequence: Arc::new(AtomicUsize::new(0)),
        mode: Arc::new(AtomicUsize::new(0)),
        attempts: Arc::new(AtomicUsize::new(0)),
        titles: Arc::new(Mutex::new(BTreeMap::new())),
        registered: Arc::new(Mutex::new(BTreeSet::new())),
        deleted: Arc::new(Mutex::new(Vec::new())),
        deleted_changed: Arc::new(tokio::sync::Notify::new()),
        blocked: Arc::new(Mutex::new(None)),
        pending_seen: Arc::new(tokio::sync::Notify::new()),
        calls: Arc::new(Mutex::new(Vec::new())),
        origin: origin.to_owned(),
    }
}

pub(super) fn router(state: FixtureState) -> Router {
    Router::new()
        .route(
            "/",
            get(|| async { r#"{"SNlM0e":"csrf","FdrFJe":"session","cfb2h":"build"}"# }),
        )
        .route(RPC_PATH, post(rpc))
        .route("/upload/_/", post(upload))
        .with_state(state)
}

async fn rpc(
    State(state): State<FixtureState>,
    Query(query): Query<BTreeMap<String, String>>,
    Form(form): Form<BTreeMap<String, String>>,
) -> Response {
    let request: Value = serde_json::from_str(&form["f.req"]).unwrap();
    let method = request[0][0][0].as_str().unwrap();
    state.calls.lock().unwrap().push(method.to_owned());
    let params: Value = serde_json::from_str(request[0][0][1].as_str().unwrap()).unwrap();
    let mode = state.mode.load(Ordering::SeqCst);
    let value = match method {
        "CCqFvf" => create(&state, &params),
        "rLM1Ne" => {
            source_rows(&state, &params[0], mode).map(|rows| json!([["Study", rows, params[0]]]))
        }
        "o4cbdc" => {
            state
                .registered
                .lock()
                .unwrap()
                .insert(params[1].as_str().unwrap().to_owned());
            Ok(json!({"SOURCE_ID":"uploaded-source","SOURCE_NAME":params[0][0][0]}))
        }
        "R7cb6c" => generate(&state, &query, &params),
        "gArtLc" => {
            if mode == 14 {
                return StatusCode::BAD_GATEWAY.into_response();
            }
            let blocked =
                state.blocked.lock().unwrap().as_deref() == Some(query["source-path"].as_str());
            let pending = mode == 3 || blocked;
            if pending {
                state.pending_seen.notify_one();
            }
            Ok(json!([[artifact_row(
                "artifact-1",
                if mode == 12 {
                    4
                } else if pending {
                    1
                } else {
                    3
                }
            )]]))
        }
        "v9rmvd" => download(mode, &query),
        "WWINqb" => delete(&state, &params),
        _ => panic!("Unexpected RPC method"),
    };
    match value {
        Ok(value) => framed(method, &value).into_response(),
        Err(status) => status.into_response(),
    }
}

fn source_rows(state: &FixtureState, notebook: &Value, mode: usize) -> Result<Value, StatusCode> {
    if !state
        .registered
        .lock()
        .unwrap()
        .contains(notebook.as_str().unwrap())
    {
        return Ok(json!([]));
    }
    if mode == 13 {
        return Err(StatusCode::BAD_GATEWAY);
    }
    let status = if mode == 11 { 3 } else { 2 };
    Ok(json!([[
        ["uploaded-source"],
        "part.pdf",
        null,
        [null, status]
    ]]))
}

fn create(state: &FixtureState, params: &Value) -> Result<Value, StatusCode> {
    if state.mode.load(Ordering::SeqCst) == 9 {
        state.attempts.fetch_add(1, Ordering::SeqCst);
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    let id = format!("owned-{}", state.sequence.fetch_add(1, Ordering::SeqCst));
    state
        .titles
        .lock()
        .unwrap()
        .insert(id.clone(), params[0].as_str().unwrap().to_owned());
    Ok(json!([params[0], [], id]))
}

fn generate(
    state: &FixtureState,
    query: &BTreeMap<String, String>,
    params: &Value,
) -> Result<Value, StatusCode> {
    let mode = state.mode.load(Ordering::SeqCst);
    let instructions = if mode == 15 {
        CUSTOM_INSTRUCTIONS
    } else {
        DEFAULT_INSTRUCTIONS
    };
    let title = state.titles.lock().unwrap()
        [query["source-path"].strip_prefix("/notebook/").unwrap()]
    .clone();
    let expected = match title.rsplit_once("_chunk") {
        Some((_, index)) => {
            format!("{instructions}\n\nCONTEXT: This is part {index} of 3 of the document.")
        }
        None => instructions.to_owned(),
    };
    assert_eq!(
        params.pointer("/2/9/1/2").unwrap().as_str().unwrap(),
        expected
    );
    assert_eq!(query["hl"], "pt_BR");
    let attempt = state.attempts.fetch_add(1, Ordering::SeqCst);
    if matches!(mode, 8 | 10) || (mode == 7 && attempt < 2) {
        return Err(StatusCode::TOO_MANY_REQUESTS);
    }
    Ok(json!([artifact_row("artifact-1", 1)]))
}

fn download(mode: usize, query: &BTreeMap<String, String>) -> Result<Value, StatusCode> {
    if mode == 1
        || (mode >= 100 && query["source-path"] == format!("/notebook/owned-{}", mode - 100))
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    let data = match mode {
        5 | 16 => json!({"flashcards":[]}),
        6 | 17 => json!({"flashcards":[{"question":"A {{c1::mitocôndria}} produz ATP celular.","answer":"Energia"}]}),
        _ => json!({"flashcards":[
            {"question":"A {{c2::mitocôndria}} produz ATP celular.","answer":"Energia para células"},
            {"question":"A {{c2::mitocôndria}} produz ATP celular.","answer":"Duplicata removida"},
            {"question":"Texto {{c1::the}} trivial.","answer":"Não deve permanecer"}
        ]}),
    }.to_string();
    let html = format!(
        "<div data-app-data=\"{}\"></div>",
        html_escape::encode_double_quoted_attribute(&data)
    );
    Ok(json!([[
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        [html]
    ]]))
}

fn delete(state: &FixtureState, params: &Value) -> Result<Value, StatusCode> {
    let id = params[0][0].as_str().unwrap();
    assert!(
        id.starts_with("owned-"),
        "Existing notebooks must never be deleted"
    );
    state.deleted.lock().unwrap().push(id.to_owned());
    state.deleted_changed.notify_one();
    if matches!(state.mode.load(Ordering::SeqCst), 2 | 10 | 16 | 17) {
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }
    Ok(Value::Null)
}

async fn upload(State(state): State<FixtureState>, headers: HeaderMap, body: Bytes) -> Response {
    match headers["x-goog-upload-command"].to_str().unwrap() {
        "start" => (
            StatusCode::OK,
            [(
                "x-goog-upload-url",
                format!("{}/upload/_/?upload_id=fixture", state.origin),
            )],
            "",
        )
            .into_response(),
        "upload, finalize" => {
            assert!(body.as_ref() == b"synthetic-pdf" || body.starts_with(b"%PDF-"));
            StatusCode::OK.into_response()
        }
        _ => panic!("Unexpected upload command"),
    }
}
