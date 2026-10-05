use super::*;

#[test]
fn accepts_only_opaque_lowercase_hexadecimal_identities() {
    assert_eq!(
        UserId::try_from("0123456789abcdef".repeat(2))
            .unwrap()
            .as_str(),
        "0123456789abcdef0123456789abcdef"
    );
    for invalid in [
        String::new(),
        "a".repeat(31),
        "a".repeat(33),
        "A".repeat(32),
        "é".repeat(16),
        "../".repeat(10),
        "g".repeat(32),
    ] {
        assert_eq!(UserId::try_from(invalid), Err(InvalidUserId));
    }
}
