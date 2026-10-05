use super::*;
use axum::{Json, Router, body::Body, response::IntoResponse, routing::post};
use flashcards_domain::Flashcard;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};

#[test]
fn accepts_only_local_endpoints_without_credentials_redirect_paths_or_proxies() {
    for endpoint in [
        DEFAULT_ANKI_CONNECT_URL,
        "http://localhost:8765/",
        "http://[::1]:8765",
        "http://127.0.0.2:9000",
    ] {
        assert!(AnkiConnectClient::new(endpoint, None).is_ok());
    }
    for endpoint in [
        "",
        "https://127.0.0.1",
        "http://example.com",
        "http://192.168.1.2:8765",
        "http://127.0.0.1/path",
        "http://key@localhost",
        "http://:private-password@127.0.0.1:8765",
        "http://localhost?key=secret",
        "http://localhost#fragment",
        "ftp://localhost",
    ] {
        let Err(error) = AnkiConnectClient::new(endpoint, None) else {
            panic!("Unsafe AnkiConnect endpoints must be rejected")
        };
        assert!(matches!(error, AnkiError::InvalidInput));
        assert_eq!(error.to_string(), "Invalid local AnkiConnect input");
        assert!(error.source().is_none());
    }
    assert!(matches!(
        AnkiConnectClient::new(DEFAULT_ANKI_CONNECT_URL, Some(String::new())),
        Err(AnkiError::InvalidInput)
    ));
}

#[tokio::test]
async fn preserves_python_cloze_fields_and_exposes_partial_imports_without_retrying() {
    let calls = Arc::new(Mutex::new(Vec::<Value>::new()));
    let mode = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route("/", post(import_response))
        .with_state((calls.clone(), mode.clone()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = AnkiConnectClient::new(&endpoint, Some("private-key".into())).unwrap();
    assert_eq!(client.version().await.unwrap(), 6);
    assert_eq!(
        client.deck_names().await.unwrap(),
        ["Default", "Estácio::Computação"]
    );
    assert_eq!(client.find_notes("deck:Default").await.unwrap(), [11, 12]);
    assert_eq!(
        client.model_field_names("Cloze").await.unwrap(),
        ["Text", "Extra"]
    );
    let mut deck = Deck::new("Local only".into());
    deck.add_flashcard(Flashcard {
        front: "{{c1::ATP}} usa $x^2$.".into(),
        back: "$$E=mc^2$$".into(),
        tags: vec!["estácio".into()],
        source: "private-source.pdf".into(),
    });
    deck.add_flashcard(Flashcard {
        front: "{{c1::DNA}} conserva informação.".into(),
        ..Flashcard::default()
    });
    assert_eq!(client.export_cloze(&deck, "Study").await.unwrap(), [21, 22]);
    let request = calls.lock().unwrap().last().unwrap().clone();
    assert_eq!(
        request["params"]["notes"][0],
        json!({
            "deckName":"Study", "modelName":"Cloze", "fields":{"Text":"{{c1::ATP}} usa \\(x^2\\).", "Extra":"\\[E=mc^2\\]"},
            "tags":["estácio"], "options":{"allowDuplicate":false,"duplicateScope":"deck","duplicateScopeOptions":{"deckName":"Study","checkChildren":false,"checkAllModels":false}}
        })
    );
    assert!(!request.to_string().contains("private-source.pdf"));
    for selected in 1..=6 {
        mode.store(selected, Ordering::SeqCst);
        let before = calls.lock().unwrap().len();
        let error = client.export_cloze(&deck, "Study").await.unwrap_err();
        assert_import_failure(selected, error);
        assert_eq!(calls.lock().unwrap().len(), before + 2);
    }
    let before = calls.lock().unwrap().len();
    assert!(client.export_cloze(&deck, "\n").await.is_err());
    assert!(client.find_notes("\0").await.is_err());
    assert!(client.model_field_names("").await.is_err());
    assert!(matches!(
        client.model_field_names(&"é".repeat(1001)).await,
        Err(AnkiError::InvalidInput)
    ));
    let mut excessive = Deck::new("Study".into());
    excessive.flashcards = vec![Flashcard::default(); 10_001];
    assert!(matches!(
        client.export_cloze(&excessive, "Study").await,
        Err(AnkiError::InvalidInput)
    ));
    deck.flashcards[0].back = "x".repeat(MAX_REQUEST_BYTES);
    assert!(matches!(
        client.export_cloze(&deck, "Study").await,
        Err(AnkiError::TooLarge)
    ));
    assert_eq!(calls.lock().unwrap().len(), before);
    assert_eq!(
        client
            .export_cloze(&Deck::new("Empty".into()), "Study")
            .await
            .unwrap(),
        Vec::<u64>::new()
    );
    assert_eq!(calls.lock().unwrap().len(), before + 1);
    server.abort();
}

#[tokio::test]
async fn rejects_untrusted_envelopes_and_bounds_chunked_responses_and_timeouts() {
    let mode = Arc::new(AtomicUsize::new(0));
    let calls = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route("/", post(envelope_response))
        .with_state((mode.clone(), calls.clone()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = AnkiConnectClient::new(&endpoint, None).unwrap();
    for selected in 0..=9 {
        mode.store(selected, Ordering::SeqCst);
        let error = client.version().await.unwrap_err();
        assert!(!error.to_string().contains("secret-value"));
        assert_eq!(error.source().is_some(), matches!(selected, 3 | 6));
        match selected {
            0..=2 | 7..=9 => assert!(matches!(error, AnkiError::Protocol)),
            3 => assert!(matches!(error, AnkiError::Json(_))),
            4 => assert!(matches!(error, AnkiError::Status(StatusCode::FOUND))),
            5 => assert!(matches!(error, AnkiError::TooLarge)),
            _ => assert!(matches!(error, AnkiError::Http(error) if error.is_timeout())),
        }
        assert_eq!(calls.load(Ordering::SeqCst), selected + 1);
    }
    server.abort();
}

type ImportState = (Arc<Mutex<Vec<Value>>>, Arc<AtomicUsize>);

async fn import_response(
    axum::extract::State((calls, mode)): axum::extract::State<ImportState>,
    Json(request): Json<Value>,
) -> axum::response::Response {
    assert_eq!(request["version"], 6);
    assert_eq!(request["key"], "private-key");
    calls.lock().unwrap().push(request.clone());
    let result = match request["action"].as_str().unwrap() {
        "version" => json!(6),
        "deckNames" => json!(["Default", "Estácio::Computação"]),
        "findNotes" => {
            assert_eq!(request["params"]["query"], "deck:Default");
            json!([11, 12])
        }
        "modelFieldNames" => {
            assert_eq!(request["params"]["modelName"], "Cloze");
            json!(["Text", "Extra"])
        }
        "createDeck" => {
            assert_eq!(request["params"]["deck"], "Study");
            json!(15)
        }
        "addNotes" => match mode.load(Ordering::SeqCst) {
            0 => json!([21, 22]),
            1 => json!([21, null]),
            2 => json!([21]),
            3 => json!([true, 22]),
            4 => return StatusCode::SERVICE_UNAVAILABLE.into_response(),
            6 => json!([21, 0]),
            _ => {
                return Json(json!({"result":null,"error":"private card content and credentials"}))
                    .into_response();
            }
        },
        _ => panic!("Unexpected AnkiConnect action"),
    };
    Json(json!({"result":result,"error":null})).into_response()
}

async fn envelope_response(
    axum::extract::State((mode, calls)): axum::extract::State<(Arc<AtomicUsize>, Arc<AtomicUsize>)>,
) -> axum::response::Response {
    calls.fetch_add(1, Ordering::SeqCst);
    match mode.load(Ordering::SeqCst) {
        0 => Json(json!({"result":6})).into_response(),
        1 => Json(json!({"result":6,"error":false})).into_response(),
        2 => Json(json!({"result":"secret-value","error":null})).into_response(),
        3 => "not JSON".into_response(),
        4 => axum::response::Response::builder()
            .status(302)
            .header("location", "http://example.com")
            .body(Body::empty())
            .unwrap(),
        5 => axum::response::Response::new(Body::from_stream(futures_util::stream::iter([
            Ok::<_, std::io::Error>(vec![b'x'; MAX_RESPONSE_BYTES]),
            Ok(vec![b'x']),
        ]))),
        7 => Json(Value::Null).into_response(),
        8 => Json(json!({"error":null})).into_response(),
        9 => Json(json!([])).into_response(),
        _ => std::future::pending().await,
    }
}

fn assert_import_failure(selected: usize, error: AnkiError) {
    assert!(!error.to_string().contains("private"));
    assert!(!error.to_string().contains("21"));
    assert!(error.source().is_none());
    match selected {
        1 => assert!(
            matches!(error, AnkiError::PartialImport { note_ids } if note_ids == [Some(21), None])
        ),
        2 | 3 | 6 => assert!(matches!(error, AnkiError::Protocol)),
        4 => assert!(matches!(
            error,
            AnkiError::Status(StatusCode::SERVICE_UNAVAILABLE)
        )),
        _ => {
            assert!(!error.to_string().contains("private"));
            assert!(matches!(error, AnkiError::Rejected));
        }
    }
}

#[tokio::test]
async fn rejects_invalid_read_results_and_stops_imports_when_deck_creation_is_unconfirmed() {
    let result = Arc::new(Mutex::new(Value::Null));
    let calls = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route("/", post(configurable_result))
        .with_state((result.clone(), calls.clone()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let client = AnkiConnectClient::new(&endpoint, None).unwrap();
    for invalid in [
        json!(null),
        json!(false),
        json!([1]),
        json!([""]),
        json!(["private\nname"]),
        json!(["é".repeat(1001)]),
    ] {
        *result.lock().unwrap() = invalid;
        assert!(matches!(
            client.deck_names().await,
            Err(AnkiError::Protocol)
        ));
        assert!(matches!(
            client.model_field_names("Cloze").await,
            Err(AnkiError::Protocol)
        ));
    }
    for invalid in [
        json!([0]),
        json!([-1]),
        json!([null]),
        json!(["private-id"]),
    ] {
        *result.lock().unwrap() = invalid;
        assert!(matches!(
            client.find_notes("deck:Study").await,
            Err(AnkiError::Protocol)
        ));
    }
    let mut deck = Deck::new("Study".into());
    deck.add_flashcard(Flashcard {
        front: "{{c1::synthetic}}".into(),
        ..Flashcard::default()
    });
    for invalid in [
        json!(0),
        json!(null),
        json!(false),
        json!("private-deck-id"),
    ] {
        *result.lock().unwrap() = invalid;
        let before = calls.load(Ordering::SeqCst);
        assert!(matches!(
            client.export_cloze(&deck, "Study").await,
            Err(AnkiError::Protocol)
        ));
        assert_eq!(calls.load(Ordering::SeqCst), before + 1);
    }
    let before = calls.load(Ordering::SeqCst);
    assert!(matches!(
        client.find_notes(&"x".repeat(16_385)).await,
        Err(AnkiError::InvalidInput)
    ));
    assert!(matches!(
        client
            .invoke("findNotes", json!({"query": "x".repeat(MAX_REQUEST_BYTES)}))
            .await,
        Err(AnkiError::TooLarge)
    ));
    assert_eq!(calls.load(Ordering::SeqCst), before);
    assert!(matches!(
        AnkiConnectClient::new(&endpoint, Some("x".repeat(4097))),
        Err(AnkiError::InvalidInput)
    ));
    server.abort();
    assert!(server.await.unwrap_err().is_cancelled());
}

async fn configurable_result(
    axum::extract::State((result, calls)): axum::extract::State<(
        Arc<Mutex<Value>>,
        Arc<AtomicUsize>,
    )>,
    Json(request): Json<Value>,
) -> Json<Value> {
    assert!(request.get("key").is_none());
    assert_eq!(request["version"], 6);
    assert_ne!(request["action"], "addNotes");
    calls.fetch_add(1, Ordering::SeqCst);
    Json(json!({"result": result.lock().unwrap().clone(), "error": null}))
}

#[tokio::test]
async fn preserves_a_truncated_http_body_error_without_retrying_an_import() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(truncate_creation_response(listener));
    let client = AnkiConnectClient::new(&endpoint, None).unwrap();
    let mut deck = Deck::new("Study".into());
    deck.add_flashcard(Flashcard {
        front: "{{c1::synthetic}}".into(),
        ..Flashcard::default()
    });
    let error = tokio::time::timeout(Duration::from_secs(5), client.export_cloze(&deck, "Study"))
        .await
        .unwrap()
        .unwrap_err();
    assert!(matches!(&error, AnkiError::Http(cause) if cause.is_decode()));
    assert!(error.source().is_some());
    for private in ["private-card-content", &endpoint] {
        assert!(!error.to_string().contains(private));
    }
    server.await.unwrap();
}

async fn truncate_creation_response(listener: tokio::net::TcpListener) {
    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
    let (socket, _) = listener.accept().await.unwrap();
    let mut socket = BufReader::new(socket);
    let mut headers = Vec::new();
    loop {
        assert!(socket.read_until(b'\n', &mut headers).await.unwrap() > 0);
        assert!(headers.len() <= 8192);
        if headers.ends_with(b"\r\n\r\n") {
            break;
        }
    }
    let length = std::str::from_utf8(&headers)
        .unwrap()
        .lines()
        .filter_map(|line| line.split_once(':'))
        .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
        .unwrap()
        .1
        .trim()
        .parse::<usize>()
        .unwrap();
    assert!(length <= 8192);
    let mut request = vec![0; length];
    socket.read_exact(&mut request).await.unwrap();
    let request: Value = serde_json::from_slice(&request).unwrap();
    assert_eq!(request["action"], "createDeck");
    socket.get_mut().write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\n{\"private-card-content\":").await.unwrap();
    socket.get_mut().shutdown().await.unwrap();
}
