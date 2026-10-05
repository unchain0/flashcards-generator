use super::*;
use serde_json::json;

#[test]
fn form_field_validation_precedes_values_and_keeps_the_existing_error_order() {
    for fields in [
        vec![
            ("language", "private/invalid"),
            ("timeout", "private-invalid"),
            ("email", "private-email"),
        ],
        vec![
            ("language", "private/invalid"),
            ("timeout", "private-invalid"),
            ("timeout", "900"),
        ],
    ] {
        let fields = fields
            .into_iter()
            .map(|(key, value)| (key.into(), value.into()))
            .collect::<Vec<_>>();
        let error = GenerationOptions::from_form(&fields)
            .unwrap_err()
            .to_string();
        assert!(error.contains("unknown or repeated field"));
        assert!(!error.contains("private"));
    }
    for fields in [
        [
            ("timeout", "private-invalid"),
            ("difficulty", "private-invalid"),
            ("language", "private/invalid"),
        ],
        [
            ("language", "private/invalid"),
            ("difficulty", "private-invalid"),
            ("timeout", "private-invalid"),
        ],
    ] {
        let fields = fields
            .into_iter()
            .map(|(key, value)| (key.into(), value.into()))
            .collect::<Vec<_>>();
        let error = GenerationOptions::from_form(&fields)
            .unwrap_err()
            .to_string();
        assert!(error.ends_with("difficulty"));
        assert!(!error.contains("private"));
    }
}

#[test]
fn accepts_browser_options_and_preserves_unicode_instruction_limits() {
    let defaults: GenerationOptions = serde_json::from_value(json!({})).unwrap();
    assert_eq!(defaults, GenerationOptions::default());
    assert_eq!(defaults.language(), "pt_BR");
    assert_eq!(defaults.timeout(), 900);
    assert_eq!(defaults.difficulty().as_str(), "medium");
    assert_eq!(defaults.quantity().as_str(), "standard");
    assert!(!defaults.single_cloze());
    assert_eq!(defaults.instructions(), "");
    let fields = [
        ("language", "en_US"),
        ("difficulty", "hard"),
        ("quantity", "more"),
        ("timeout", "7200"),
        ("single_cloze", "true"),
        ("instructions", "Português e inglês"),
    ]
    .into_iter()
    .map(|(key, value)| (key.into(), value.into()))
    .collect::<Vec<_>>();
    let options = GenerationOptions::from_form(&fields).unwrap();
    assert_eq!(options.language(), "en_US");
    assert_eq!(options.difficulty(), Difficulty::Hard);
    assert_eq!(options.quantity(), Quantity::More);
    assert!(options.single_cloze());
    assert_eq!(options.instructions(), "Português e inglês");
    assert!(
        serde_json::from_value::<GenerationOptions>(
            json!({"instructions":"á".repeat(10000),"timeout":30})
        )
        .is_ok()
    );
    assert!(
        serde_json::from_value::<GenerationOptions>(json!({"instructions":"á".repeat(10001)}))
            .is_err()
    );
}

#[test]
fn rejects_unknown_duplicate_and_invalid_fields_at_both_boundaries() {
    for input in [
        json!({"language":"x"}),
        json!({"language":"x".repeat(33)}),
        json!({"language":"pt/BR"}),
        json!({"language":"日本語"}),
        json!({"timeout":29}),
        json!({"timeout":7201}),
        json!({"timeout":-1}),
        json!({"timeout":30.5}),
        json!({"difficulty":"impossible"}),
        json!({"quantity":"many"}),
        json!({"single_cloze":"true"}),
        json!({"email":"not-stored"}),
    ] {
        assert!(serde_json::from_value::<GenerationOptions>(input).is_err());
    }
    for (key, value) in [
        ("difficulty", "bad"),
        ("quantity", "bad"),
        ("timeout", "bad"),
        ("timeout", "0"),
        ("single_cloze", "yes"),
        ("email", "not-stored"),
    ] {
        assert!(GenerationOptions::from_form(&[(key.into(), value.into())]).is_err());
    }
    assert!(
        GenerationOptions::from_form(&[
            ("language".into(), "pt_BR".into()),
            ("language".into(), "en_US".into())
        ])
        .is_err()
    );
    for difficulty in [Difficulty::Easy, Difficulty::Medium, Difficulty::Hard] {
        assert_ne!(difficulty.as_str(), "");
    }
    for quantity in [Quantity::Fewer, Quantity::Standard, Quantity::More] {
        assert_ne!(quantity.as_str(), "");
    }
}

#[test]
fn form_validation_errors_identify_the_field_without_disclosing_its_contents() {
    for (key, value) in [
        ("language", "private/invalid-language".to_owned()),
        ("timeout", "private-invalid-timeout".to_owned()),
        ("difficulty", "private-invalid-difficulty".to_owned()),
        ("quantity", "private-invalid-quantity".to_owned()),
        ("single_cloze", "private-invalid-boolean".to_owned()),
        ("instructions", "private-instruction".repeat(600)),
    ] {
        let error = GenerationOptions::from_form(&[(key.into(), value.clone())]).unwrap_err();
        assert!(error.to_string().contains(key));
        assert!(!error.to_string().contains("private-"));
        assert!(!error.to_string().contains(&value));
        assert!(error.source().is_none());
    }
}

#[test]
fn form_choices_match_the_json_contract_for_each_supported_difficulty_and_quantity() {
    for (difficulty, quantity, timeout, single_cloze) in [
        ("easy", "fewer", 30, false),
        ("medium", "standard", 900, true),
        ("hard", "more", 7200, false),
    ] {
        let json = json!({
            "difficulty": difficulty, "quantity": quantity, "timeout": timeout,
            "single_cloze": single_cloze, "instructions": "Compare DNA and RNA.",
            "language": "en-US"
        });
        let fields = vec![
            ("difficulty".into(), difficulty.into()),
            ("quantity".into(), quantity.into()),
            ("timeout".into(), timeout.to_string()),
            ("single_cloze".into(), single_cloze.to_string()),
            ("instructions".into(), "Compare DNA and RNA.".into()),
            ("language".into(), "en-US".into()),
        ];
        let form = GenerationOptions::from_form(&fields).unwrap();
        assert_eq!(form, serde_json::from_value(json).unwrap());
        assert_eq!(form.difficulty().as_str(), difficulty);
        assert_eq!(form.quantity().as_str(), quantity);
        assert_eq!(form.timeout(), timeout);
        assert_eq!(form.single_cloze(), single_cloze);
    }
    assert_eq!(
        GenerationOptions::from_form(&[]).unwrap(),
        GenerationOptions::default()
    );
}
