use super::*;

#[test]
fn confines_filenames_and_preserves_supported_suffixes() {
    let names = [
        "../../lesson.PDF",
        r"C:\private\slides.PPTX",
        "áé漢字.pdf",
        "my..notes.pdf",
        "notes.pdf/",
    ]
    .map(String::from);
    let names = safe_filenames(&names).unwrap();
    assert_eq!(
        names.iter().map(SourceFilename::as_str).collect::<Vec<_>>(),
        vec![
            "01-lesson.pdf",
            "02-slides.pptx",
            "03-document.pdf",
            "04-my-notes.pdf",
            "05-notes.pdf"
        ]
    );
    assert_eq!(
        safe_filenames(&[format!("{}.pdf", "a".repeat(100))]).unwrap()[0]
            .as_str()
            .len(),
        87
    );
    assert_eq!(
        safe_filenames(&["same.pdf".into(), "same.pdf".into()]).unwrap()[1].as_str(),
        "02-same.pdf"
    );
}

#[test]
fn rejects_empty_excessive_and_unsupported_upload_lists() {
    assert_eq!(safe_filenames(&[]), Err(InvalidDocumentInput::FileCount));
    assert_eq!(
        safe_filenames(&vec!["source.pdf".into(); 11]),
        Err(InvalidDocumentInput::FileCount)
    );
    assert!(safe_filenames(&vec!["source.pdf".into(); 10]).is_ok());
    for name in [
        "",
        "..",
        ".pdf",
        "notes.exe",
        "notes.pdf.exe",
        "notes.pdf ",
        "notes.",
    ] {
        assert_eq!(
            safe_filenames(&[name.into()]),
            Err(InvalidDocumentInput::FileType)
        );
    }
}
