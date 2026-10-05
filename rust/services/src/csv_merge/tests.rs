use super::*;
#[test]
fn accepts_only_output_basenames() {
    for name in [DEFAULT_MERGED_FILENAME, "Anki português.csv"] {
        assert!(MergeCsvRequest::new(PathBuf::from("."), name.into(), true, false).is_ok());
    }
    for name in [
        "",
        ".",
        "..",
        "/tmp/output.csv",
        "../output.csv",
        "nested/output.csv",
        "nested\\output.csv",
        "bad\n.csv",
        "bad\0.csv",
    ] {
        let error = MergeCsvRequest::new(PathBuf::from("."), name.into(), true, false)
            .err()
            .unwrap();
        assert!(!error.to_string().contains("/tmp/output.csv"));
        assert!(error.source().is_none());
    }
    assert!(MergeCsvRequest::new(PathBuf::from("."), "a".repeat(255), false, true).is_ok());
    assert!(MergeCsvRequest::new(PathBuf::from("."), "a".repeat(256), false, true).is_err());
}
