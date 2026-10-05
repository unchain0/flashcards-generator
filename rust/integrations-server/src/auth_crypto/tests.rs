use super::*;

const TEST_SECRET: &str = "test-lookup-secret-0123456789abcdef";

fn crypto() -> AuthCrypto {
    AuthCrypto::new(TEST_SECRET, TEST_SECRET).expect("test secrets meet minimum length")
}

#[test]
fn preserves_python_lookup_and_password_hashes() {
    let crypto = crypto();
    assert_eq!(
        crypto.password_lookup("senha-de-teste-123"),
        "02f70f8c5e1d016742180185afbec38234a8fcceed79af622aba2e9d1e418409"
    );
    let python_hash = "$argon2id$v=19$m=65536,t=3,p=4$MDEyMzQ1Njc4OWFiY2RlZg$kKr72i2WB9JrNMB9uauJyw/68h0u+OMS+tTC13fIk4A";
    assert!(crypto.verify_password(python_hash, "senha-de-teste-123"));
    assert!(!crypto.password_needs_rehash(python_hash));
    assert!(crypto.password_needs_rehash("malformed"));
    assert!(!crypto.verify_password(python_hash, "wrong-password"));
    assert!(!crypto.verify_password("malformed", "senha-de-teste-123"));
    assert!(!crypto.verify_password(python_hash, ""));
    assert!(!crypto.verify_password(python_hash, &"a".repeat(257)));
}

#[test]
fn enforces_character_limits_and_randomizes_salts_and_sessions() {
    assert!(AuthCrypto::new("short", TEST_SECRET).is_err());
    assert!(AuthCrypto::new(TEST_SECRET, "short").is_err());
    assert!(AuthCrypto::validate_password(&"á".repeat(12)).is_ok());
    assert!(AuthCrypto::validate_password(&"a".repeat(256)).is_ok());
    assert!(AuthCrypto::validate_password(&"a".repeat(11)).is_err());
    assert!(AuthCrypto::validate_password(&"a".repeat(257)).is_err());
    let crypto = crypto();
    assert!(crypto.hash_password("short").is_err());
    let hash = crypto
        .hash_password("senha-de-teste-123")
        .expect("hashing works");
    let other = crypto
        .hash_password("senha-de-teste-123")
        .expect("hashing works");
    assert_ne!(hash, other);
    assert!(crypto.verify_password(&hash, "senha-de-teste-123"));
    let token = crypto.new_session_token().expect("OS entropy available");
    assert_eq!(token.len(), 43);
    assert_ne!(
        token,
        crypto.new_session_token().expect("OS entropy available")
    );
    assert_eq!(crypto.session_hash(&token).len(), 64);
}

#[test]
fn authenticates_companion_claims_and_rejects_tampering_and_expiration() {
    let crypto = crypto();
    let token = crypto
        .create_companion_token(&"a".repeat(32), "session-token", 1000)
        .expect("claims encode");
    let claims = crypto
        .verified_companion_claims(&token, 1000)
        .expect("valid token");
    assert_eq!(claims.subject, "a".repeat(32));
    assert_eq!(claims.session_hash, crypto.session_hash("session-token"));
    assert_eq!(claims.expires_at, 1300);
    assert!(crypto.verified_companion_claims(&token, 1299).is_some());
    assert!(crypto.verified_companion_claims(&token, 999).is_none());
    assert!(crypto.verified_companion_claims(&token, 1300).is_none());
    assert!(
        crypto
            .verified_companion_claims(&format!("{token}x"), 1000)
            .is_none()
    );
    assert!(
        crypto
            .verified_companion_claims("malformed", 1000)
            .is_none()
    );
    assert!(
        crypto
            .verified_companion_claims(&"a".repeat(4097), 1000)
            .is_none()
    );
    let other = AuthCrypto::new(&"z".repeat(32), TEST_SECRET).expect("valid secrets");
    assert!(other.verified_companion_claims(&token, 1000).is_none());
}

fn signed_payload(crypto: &AuthCrypto, payload: &str) -> String {
    let signature = crypto.companion_mac(payload).finalize().into_bytes();
    format!("{payload}.{}", URL_SAFE_NO_PAD.encode(signature))
}

#[test]
fn rejects_authenticated_payloads_with_invalid_encoding_or_claims() {
    let crypto = crypto();
    let token = crypto
        .create_companion_token(&"a".repeat(32), "session", 1000)
        .unwrap();
    let (payload, _) = token.split_once('.').unwrap();
    let valid: serde_json::Value =
        serde_json::from_slice(&URL_SAFE_NO_PAD.decode(payload).unwrap()).unwrap();
    for (field, value) in [
        ("audience", serde_json::json!("another-service")),
        ("issued_at", serde_json::json!(1001)),
        ("expires_at", serde_json::json!(1000)),
        ("expires_at", serde_json::json!(1301)),
        ("session_hash", serde_json::json!("a".repeat(63))),
        ("subject", serde_json::json!("a".repeat(31))),
        ("issued_at", serde_json::json!(-1)),
        ("nonce", serde_json::json!(null)),
        ("extra_identity", serde_json::json!("private@example.test")),
    ] {
        let mut invalid = valid.clone();
        invalid[field] = value;
        let payload = URL_SAFE_NO_PAD.encode(serde_json::to_vec(&invalid).unwrap());
        assert!(
            crypto
                .verified_companion_claims(&signed_payload(&crypto, &payload), 1000)
                .is_none(),
            "{field}"
        );
    }
    for invalid in ["%", "_w", "bm90LWpzb24"] {
        assert!(
            crypto
                .verified_companion_claims(&signed_payload(&crypto, invalid), 1000)
                .is_none()
        );
    }
    for invalid in [
        format!("{payload}.%"),
        format!("{payload}.eA"),
        format!("{token}.extra"),
    ] {
        assert!(crypto.verified_companion_claims(&invalid, 1000).is_none());
    }
    let mut bare_mac = crypto.session_mac.clone();
    bare_mac.update(payload.as_bytes());
    let bare_token = format!(
        "{payload}.{}",
        URL_SAFE_NO_PAD.encode(bare_mac.finalize().into_bytes())
    );
    assert!(
        crypto
            .verified_companion_claims(&bare_token, 1000)
            .is_none()
    );
}

#[test]
fn detects_each_legacy_hash_policy_difference() {
    let crypto = crypto();
    let hash = "$argon2id$v=19$m=65536,t=3,p=4$MDEyMzQ1Njc4OWFiY2RlZg$kKr72i2WB9JrNMB9uauJyw/68h0u+OMS+tTC13fIk4A";
    for (old, new) in [
        ("argon2id", "argon2i"),
        ("v=19", "v=16"),
        ("m=65536", "m=8192"),
        ("t=3", "t=1"),
        ("p=4", "p=1"),
        ("MDEyMzQ1Njc4OWFiY2RlZg", "MDEyMzQ1Njc"),
        (
            "kKr72i2WB9JrNMB9uauJyw/68h0u+OMS+tTC13fIk4A",
            "MDEyMzQ1Njc4OWFiY2RlZg",
        ),
    ] {
        let legacy = hash.replacen(old, new, 1);
        assert!(PasswordHash::new(&legacy).is_ok());
        assert!(crypto.password_needs_rehash(&legacy), "{old}");
    }
    assert!(crypto.password_needs_rehash("$argon2id$v=19$m=65536,t=3,p=4"));
    assert!(!crypto.password_needs_rehash(hash));
}

#[test]
fn retains_typed_crypto_causes_without_exposing_private_error_details() {
    let private = "private-authentication-detail";
    let errors = [
        AuthCryptoError::Parameters(Params::new(1, 1, 1, Some(32)).unwrap_err()),
        AuthCryptoError::Entropy(getrandom::Error::UNSUPPORTED),
        AuthCryptoError::Hash(password_hash::Error::Password),
        AuthCryptoError::Json(serde_json::Error::io(std::io::Error::other(private))),
    ];
    for error in errors {
        let source = error.source().expect("underlying cause remains available");
        match &error {
            AuthCryptoError::Parameters(_) => assert!(source.is::<argon2::Error>()),
            AuthCryptoError::Entropy(_) => assert!(source.is::<getrandom::Error>()),
            AuthCryptoError::Hash(_) => assert!(source.is::<password_hash::Error>()),
            AuthCryptoError::Json(_) => assert!(source.is::<serde_json::Error>()),
            AuthCryptoError::InvalidSecret | AuthCryptoError::InvalidPassword => unreachable!(),
        }
        assert!(!error.to_string().contains(private));
    }
    assert!(AuthCryptoError::InvalidPassword.source().is_none());
    assert!(AuthCryptoError::InvalidSecret.source().is_none());
    assert_ne!(AuthCryptoError::InvalidPassword.to_string(), "");
    assert_ne!(AuthCryptoError::InvalidSecret.to_string(), "");
}
