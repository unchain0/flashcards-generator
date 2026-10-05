use super::*;

fn values() -> BTreeMap<String, String> {
    [
        ("FLASHCARDS_DATABASE_URL", "postgresql://local/flashcards"),
        (
            "FLASHCARDS_SESSION_SECRET",
            "test-session-secret-0123456789abcdef",
        ),
        (
            "FLASHCARDS_AUTH_LOOKUP_SECRET",
            "test-lookup-secret-0123456789abcdef",
        ),
        ("FLASHCARDS_ENVIRONMENT", "production"),
    ]
    .into_iter()
    .map(|(key, value)| (key.into(), value.into()))
    .collect()
}

#[test]
fn validates_production_configuration_without_disclosing_values() {
    let config = WebConfig::from_values(&values()).unwrap();
    assert!(config.production);
    assert_eq!(config.port, 8000);
    assert_eq!(config.session_ttl_seconds, 2_592_000);
    assert!(config.bootstrap_password.is_none());
    for (key, value) in [
        ("FLASHCARDS_ENVIRONMENT", "unknown"),
        ("FLASHCARDS_DATABASE_URL", "sqlite:///data.db"),
        ("FLASHCARDS_PORT", "0"),
        ("FLASHCARDS_PORT", "65536"),
        ("FLASHCARDS_HOST", "hostname"),
        ("FLASHCARDS_SESSION_SECRET", "short"),
        ("FLASHCARDS_AUTO_CREATE_SCHEMA", "true"),
        ("FLASHCARDS_SESSION_TTL_SECONDS", "299"),
        ("FLASHCARDS_SESSION_TTL_SECONDS", "bad"),
        ("FLASHCARDS_CORS_ORIGINS", "not-json"),
        ("FLASHCARDS_CORS_ORIGINS", "[\"http://example.com\"]"),
        ("FLASHCARDS_CORS_ORIGINS", "[\"https://example.com/path\"]"),
        ("FLASHCARDS_CORS_ORIGINS", "[\"https://user@example.com\"]"),
    ] {
        let mut values = values();
        values.insert(key.into(), value.into());
        assert!(WebConfig::from_values(&values).is_err(), "{key}");
    }
    let mut same = values();
    same.insert(
        "FLASHCARDS_AUTH_LOOKUP_SECRET".into(),
        same["FLASHCARDS_SESSION_SECRET"].clone(),
    );
    assert!(WebConfig::from_values(&same).is_err());
    assert!(WebConfig::from_values(&BTreeMap::new()).is_err());
    assert!(parse_origin("https://example.com", true).is_ok());
    assert!(parse_origin("http://localhost:8000", false).is_ok());
}

#[test]
fn accepts_development_defaults_and_explicit_boundary_values() {
    let mut settings = values();
    settings.remove("FLASHCARDS_ENVIRONMENT");
    settings.insert("FLASHCARDS_BOOTSTRAP_PASSWORD".into(), String::new());
    let development = WebConfig::from_values(&settings).unwrap();
    assert!(!development.production);
    assert_eq!(development.host.to_string(), "127.0.0.1");
    assert!(development.bootstrap_password.is_none());

    settings.extend(
        [
            ("FLASHCARDS_ENVIRONMENT", "test"),
            ("FLASHCARDS_HOST", "::1"),
            ("FLASHCARDS_PORT", "65535"),
            ("FLASHCARDS_SESSION_TTL_SECONDS", "300"),
            (
                "FLASHCARDS_BOOTSTRAP_PASSWORD",
                "configured-access-password",
            ),
            ("FLASHCARDS_STATIC_DIR", "custom-static"),
            ("FLASHCARDS_CORS_ORIGINS", "[\"http://localhost:8000\"]"),
        ]
        .map(|(key, value)| (key.into(), value.into())),
    );
    settings.insert(
        "FLASHCARDS_AUTH_LOOKUP_SECRET".into(),
        settings["FLASHCARDS_SESSION_SECRET"].clone(),
    );
    let test = WebConfig::from_values(&settings).unwrap();
    assert!(!test.production);
    assert_eq!(test.host.to_string(), "::1");
    assert_eq!(test.port, 65535);
    assert_eq!(test.session_ttl_seconds, 300);
    assert_eq!(
        test.bootstrap_password.as_deref(),
        Some("configured-access-password")
    );
    assert_eq!(test.static_dir, PathBuf::from("custom-static"));
    assert_eq!(
        test.cors_origins,
        vec![HeaderValue::from_static("http://localhost:8000")]
    );
}

#[test]
fn rejects_missing_required_values_and_invalid_origins_with_private_diagnostics() {
    for name in [
        "FLASHCARDS_SESSION_SECRET",
        "FLASHCARDS_AUTH_LOOKUP_SECRET",
        "FLASHCARDS_DATABASE_URL",
    ] {
        let mut settings = values();
        settings.remove(name);
        let error = WebConfig::from_values(&settings).err().unwrap();
        assert!(error.to_string().contains(name));
        assert!(!format!("{error:?}").contains("0123456789abcdef"));
        assert!(error.source().is_none());
    }
    for origin in [
        "https://example.com/",
        "https://example.com?secret=private",
        "ftp://example.com",
        "https://",
        "https://example.com\nAuthorization: private",
    ] {
        let error = parse_origin(origin, true).unwrap_err();
        assert_ne!(error.to_string(), "");
        assert!(!format!("{error:?}").contains("private"));
    }
}
