use flashcards_domain::identity::UserId;
use flashcards_integrations::notebooklm_profiles::{LocalNotebookLMProfiles, ProfileError};
use serde_json::json;

#[test]
fn legacy_cookies_without_paths_round_trip_and_invalid_updates_preserve_the_session() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let user = UserId::try_from("a".repeat(32)).unwrap();
    let legacy = json!({
        "cookies": [{"name":"SID", "value":"synthetic-private-session", "domain":".google.com"}],
        "origins": [],
        "notebooklm": {"account":{"authuser":2}},
    });
    let original = legacy.to_string();
    profiles.save(&user, &original).unwrap();
    assert_eq!(
        profiles.load(&user).unwrap().as_deref(),
        Some(original.as_str())
    );

    for path in ["", "relative", "/\r\nInjected: synthetic-private-session"] {
        let mut invalid = legacy.clone();
        invalid["cookies"][0]["path"] = json!(path);
        let error = profiles.save(&user, &invalid.to_string()).unwrap_err();
        assert!(matches!(error, ProfileError::InvalidCredentials));
        assert!(!error.to_string().contains("synthetic-private-session"));
        assert_eq!(
            profiles.load(&user).unwrap().as_deref(),
            Some(original.as_str())
        );
    }
    let mut invalid = legacy.clone();
    invalid["cookies"].as_array_mut().unwrap().push(json!({
        "name":"RPC", "value":"synthetic-private-session; injected",
        "domain":"notebooklm.google.com", "path":"/_/",
    }));
    assert!(matches!(
        profiles.save(&user, &invalid.to_string()),
        Err(ProfileError::InvalidCredentials)
    ));
    assert_eq!(
        profiles.load(&user).unwrap().as_deref(),
        Some(original.as_str())
    );
    let mut explicit = legacy;
    explicit["cookies"][0]["path"] = json!("/");
    let updated = explicit.to_string();
    profiles.save(&user, &updated).unwrap();
    assert_eq!(
        profiles.load(&user).unwrap().as_deref(),
        Some(updated.as_str())
    );
}
