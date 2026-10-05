use super::*;
use reqwest::{Url, cookie::CookieStore};

#[test]
fn bounds_page_token_pattern_compilation_without_disclosing_the_key() {
    let key = format!("private-synthetic-key-{}", "x".repeat(1_048_576));
    let error = protocol::page_token("", &key).unwrap_err();
    assert!(matches!(
        error,
        NotebookLMError::Schema("page token pattern")
    ));
    assert!(!error.to_string().contains("private-synthetic-key"));
}

#[test]
fn cookie_limits_and_paths_preserve_only_valid_scoped_credentials() {
    let cookie = json!({"name":"SID","value":"private-synthetic-cookie","domain":".google.com"});
    let credentials =
        protocol::Credentials::parse(&json!({"cookies":[cookie.clone()]}).to_string()).unwrap();
    assert!(credentials.header("/").unwrap().is_sensitive());
    let jar = credentials.cookie_jar().unwrap();
    assert_eq!(
        jar.cookies(&Url::parse("https://notebook.google.com/").unwrap())
            .unwrap(),
        "SID=private-synthetic-cookie"
    );
    assert!(
        jar.cookies(&Url::parse("https://example.test/").unwrap())
            .is_none()
    );
    assert!(
        protocol::Credentials::parse(&json!({"cookies":vec![cookie.clone(); 512]}).to_string())
            .is_ok()
    );
    assert!(matches!(
        protocol::Credentials::parse(&json!({"cookies":vec![cookie.clone(); 513]}).to_string()),
        Err(NotebookLMError::ResponseTooLarge)
    ));
    for (field, value) in [
        ("name", json!("")),
        ("name", json!("a".repeat(129))),
        ("name", json!("private;invalid")),
        ("name", json!("private\ninvalid")),
        ("value", json!("a".repeat(8193))),
        ("value", json!("private\r\ninvalid")),
        ("path", json!("relative-private-path")),
        ("path", json!("/private;invalid")),
        ("path", json!("/privado-á")),
        ("expires", json!(f64::MAX)),
    ] {
        let mut invalid = cookie.clone();
        invalid[field] = value;
        let credentials =
            protocol::Credentials::parse(&json!({"cookies":[invalid]}).to_string()).unwrap();
        let error = credentials.cookie_jar().err().unwrap();
        assert!(matches!(error, NotebookLMError::Authentication));
        assert!(!format!("{error:?}").contains("private"));
    }
    let large = json!({"cookies":vec![json!({
        "name":"SID","value":"a".repeat(8192),"domain":".google.com"
    });9]});
    let credentials = protocol::Credentials::parse(&large.to_string()).unwrap();
    assert!(matches!(
        credentials.header("/"),
        Err(NotebookLMError::ResponseTooLarge)
    ));
    let scoped = protocol::Credentials::parse(
        &json!({"cookies":[
            {"name":"SID","value":"synthetic","domain":".google.com","path":"/account"},
            {"name":"OLD","value":"expired","domain":".google.com","expires":1},
            {"name":"OTHER","value":"unrelated","domain":"example.test"}
        ]})
        .to_string(),
    )
    .unwrap();
    let jar = scoped.cookie_jar().unwrap();
    assert!(
        jar.cookies(&Url::parse("https://notebook.google.com/").unwrap())
            .is_none()
    );
    assert!(
        jar.cookies(&Url::parse("https://notebook.google.com/accounts").unwrap())
            .is_none()
    );
    assert_eq!(
        jar.cookies(&Url::parse("https://notebook.google.com/account/page").unwrap())
            .unwrap(),
        "SID=synthetic"
    );
    assert!(matches!(
        scoped.header("/accounts"),
        Err(NotebookLMError::Authentication)
    ));
    assert_eq!(scoped.header("/account/page").unwrap(), "SID=synthetic");
}

#[test]
fn rejects_ambiguous_or_excessive_rpc_structures_and_keeps_valid_method_results() {
    for status in [Value::Null, json!([0])] {
        let frame = json!([["wrb.fr", "test", null, null, null, status]]);
        assert_eq!(
            protocol::decode_rpc(&frame.to_string(), "test").unwrap(),
            Value::Null
        );
    }
    assert_eq!(
        protocol::decode_rpc(
            "[[\"er\",\"other\"],42,[\"wrb.fr\",\"test\",null,null,null,[0]]]",
            "test"
        )
        .unwrap(),
        Value::Null
    );
    assert_eq!(
        protocol::decode_rpc(
            &framed("test", &json!(["ação"])).replace('\n', "\r\n"),
            "test"
        )
        .unwrap(),
        json!(["ação"])
    );
    let mut nested = json!(["wrb.fr", "test", "[]"]);
    for _ in 0..18 {
        nested = json!([nested]);
    }
    for raw in [
        nested.to_string(),
        json!([["wrb.fr", "test"]]).to_string(),
        json!([["wrb.fr", "test", "private-invalid-json"]]).to_string(),
        json!([["wrb.fr", "test", 42]]).to_string(),
    ] {
        let error = protocol::decode_rpc(&raw, "test").unwrap_err();
        assert!(!error.to_string().contains("private"));
    }
    for result in [json!(null), json!([]), json!([[[[["source-too-deep"]]]]])] {
        assert!(matches!(
            protocol::source_identifier(&result),
            Err(NotebookLMError::Schema(_))
        ));
    }
    for row in [
        json!(null),
        json!(["title", [], null]),
        json!([42, [], "valid-id"]),
    ] {
        assert!(protocol::notebook(&row).is_err());
    }
    assert!(protocol::artifact(&json!([null, null, null, null, 3])).is_err());
    for result in [json!([]), json!([[null, 42]])] {
        assert!(protocol::source_status(&result, "source").is_err());
    }
    assert_eq!(
        protocol::source_status(&json!([[null, null]]), "source").unwrap(),
        None
    );
    let oversized = "x".repeat(MAX_RESPONSE_BYTES + 1);
    assert!(matches!(
        protocol::decode_rpc(&oversized, "test"),
        Err(NotebookLMError::ResponseTooLarge)
    ));
    assert!(matches!(
        protocol::flashcards(&oversized),
        Err(NotebookLMError::ResponseTooLarge)
    ));
}

#[test]
fn token_parsing_rejects_empty_excessive_and_invalid_escaped_values() {
    for token in [String::new(), "private-token".repeat(700)] {
        let html = json!({"SNlM0e":token}).to_string();
        assert!(matches!(
            protocol::page_token(&html, "SNlM0e"),
            Err(NotebookLMError::Authentication)
        ));
    }
    let error = protocol::page_token(r#"{"SNlM0e":"private\q"}"#, "SNlM0e").unwrap_err();
    assert!(matches!(error, NotebookLMError::Json(_)));
    assert!(!error.to_string().contains("private"));
}

#[test]
fn plain_and_rich_cards_preserve_order_text_and_card_count_boundaries() {
    let front = "A {{c1::célula}} mantém o equilíbrio com $Na^+$.";
    for data in [
        json!([{"q":front,"a":"Explicação breve."}]),
        json!({"cards":[{"question":front,"answer":"Explicação breve."}]}),
    ] {
        let cards = protocol::flashcards(&cards_html(&data)).unwrap();
        assert_eq!(cards.len(), 1);
        assert_eq!(cards[0].front, front);
        assert_eq!(cards[0].back, "Explicação breve.");
    }
    let blocks = (0..128)
        .map(|index| json!({"type":"markdown","content":format!("Bloco {index}")}))
        .collect::<Vec<_>>();
    let rich = json!({"cards":[{"front":{"flashcardContentBlock":blocks},"back":"Resposta"}]});
    let cards = protocol::flashcards(&cards_html(&rich)).unwrap();
    assert_eq!(cards[0].front.lines().count(), 128);
    assert!(cards[0].front.starts_with("Bloco 0\nBloco 1\n"));
    assert!(cards[0].front.ends_with("Bloco 127"));
    let card = json!({"front":front,"back":"Resposta"});
    assert_eq!(
        protocol::flashcards(&cards_html(&json!({"cards":vec![card.clone();10_000]})))
            .unwrap()
            .len(),
        10_000
    );
    assert!(matches!(
        protocol::flashcards(&cards_html(&json!({"cards":vec![card;10_001]}))),
        Err(NotebookLMError::ResponseTooLarge)
    ));
}

fn cards_html(data: &Value) -> String {
    format!(
        "<div data-app-data=\"{}\"></div>",
        html_escape::encode_double_quoted_attribute(&data.to_string())
    )
}
