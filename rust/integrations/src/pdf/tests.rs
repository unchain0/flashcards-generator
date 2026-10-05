use super::*;
use std::fs;

#[tokio::test]
async fn pdf_diagnostics_preserve_causes_without_disclosing_document_paths() {
    let worker = tokio::spawn(std::future::pending::<()>());
    worker.abort();
    let errors = [
        PdfError::InvalidInput,
        PdfError::InvalidStructure,
        PdfError::from(io::Error::other("synthetic-private-pdf-path")),
        PdfError::Process(ProcessError::Io(io::Error::other(
            "synthetic-private-pdf-path",
        ))),
        PdfError::Worker(worker.await.unwrap_err()),
        PdfError::Timeout,
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), matches!(index, 2..=4));
        assert!(!error.to_string().contains("synthetic-private-pdf-path"));
    }
    assert_eq!(
        errors[2].source().unwrap().to_string(),
        "synthetic-private-pdf-path"
    );
    assert_eq!(
        errors[3].source().unwrap().source().unwrap().to_string(),
        "synthetic-private-pdf-path"
    );
}

#[test]
fn bounds_pdf_metadata_and_ignores_invalid_or_reversed_chapter_ranges() {
    use serde_json::json;
    let pages: Vec<_> = (1..=10_001)
        .map(|page| json!({"pageposfrom1":page}))
        .collect();
    assert!(matches!(
        parse_structure(
            &serde_json::to_vec(&json!({"version":2,"pages":pages,"outlines":[]})).unwrap()
        ),
        Err(PdfError::InvalidStructure)
    ));
    let pages = vec![PdfPage { pageposfrom1: 1 }, PdfPage { pageposfrom1: 2 }];
    let outlines = [Some(0), Some(3), None, Some(2), Some(1)]
        .into_iter()
        .map(|page| Outline {
            destpageposfrom1: page,
            title: "Section".into(),
            kids: Vec::new(),
        })
        .collect();
    let structure = PdfStructure {
        version: 2,
        pages,
        outlines,
    };
    assert_eq!(
        structure.chapters().unwrap(),
        vec![Chapter {
            pages: 0..2,
            title: "Section".into()
        }]
    );
    let oversized = Outline {
        destpageposfrom1: Some(1),
        title: "x".repeat(65_537),
        kids: Vec::new(),
    };
    assert!(matches!(
        flatten_outlines(&[oversized]),
        Err(PdfError::InvalidStructure)
    ));
    let many: Vec<_> = (0..10_001)
        .map(|_| Outline {
            destpageposfrom1: Some(1),
            title: "Section".into(),
            kids: Vec::new(),
        })
        .collect();
    assert!(matches!(
        flatten_outlines(&many),
        Err(PdfError::InvalidStructure)
    ));
    assert_eq!(flatten_outlines(&many[..10_000]).unwrap().len(), 10_000);
}

async fn page_widths(path: &Path) -> Vec<usize> {
    let result = run_bounded(
        Command::new("qpdf").arg("--json=2").arg(path),
        Duration::from_secs(30),
        1024 * 1024,
    )
    .await
    .unwrap();
    assert!(result.status.success());
    let json: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    json["pages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|page| {
            let object = page["object"].as_str().unwrap();
            usize::try_from(
                json["qpdf"][1][format!("obj:{object}")]["value"]["/MediaBox"][2]
                    .as_u64()
                    .unwrap(),
            )
            .unwrap()
                - 100
        })
        .collect()
}

#[tokio::test]
async fn reads_nested_outlines_and_writes_real_private_chunks_without_changing_sources() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("Local document.PDF");
    let original = include_bytes!("../../tests/fixtures/chapters.pdf");
    fs::write(&source, original).unwrap();
    let processor = PdfProcessor::default();
    let chapters = processor.prepare(&source, 50, true).await.unwrap();
    assert_eq!(chapters.total_pages(), 70);
    assert_eq!(chapters.files().len(), 3);
    assert_eq!(
        page_widths(&chapters.files()[0]).await,
        (0..20).collect::<Vec<_>>()
    );
    assert_eq!(
        page_widths(&chapters.files()[1]).await,
        (20..45).collect::<Vec<_>>()
    );
    assert_eq!(
        page_widths(&chapters.files()[2]).await,
        (45..70).collect::<Vec<_>>()
    );
    let directory = chapters.directory().to_owned();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&directory).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_private_chunks(chapters.files());
    }
    drop(chapters);
    assert!(!directory.exists());
    let fixed = processor.prepare(&source, 50, false).await.unwrap();
    assert_eq!(fixed.files().len(), 3);
    for (path, expected) in fixed.files().iter().zip([0..30, 25..55, 50..70]) {
        assert_eq!(page_widths(path).await, expected.collect::<Vec<_>>());
    }
    let unsplit = processor.prepare(&source, 70, true).await.unwrap();
    assert_eq!(unsplit.files().len(), 1);
    assert_eq!(
        page_widths(&unsplit.files()[0]).await,
        (0..70).collect::<Vec<_>>()
    );
    assert_ne!(unsplit.files()[0], source);
    assert_eq!(fs::read(&source).unwrap(), original);
    assert_eq!(fs::read_dir(temporary.path()).unwrap().count(), 1);
}

#[cfg(unix)]
fn assert_private_chunks(files: &[PathBuf]) {
    use std::os::unix::fs::PermissionsExt;
    for path in files {
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}

#[tokio::test]
async fn rejects_invalid_sources_and_bounded_metadata() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("invalid.pdf");
    let processor = PdfProcessor::default();
    for content in [b"".as_slice(), b"%PDF", b"invalid", b"%PDF-corrupt"] {
        fs::write(&source, content).unwrap();
        assert!(processor.prepare(&source, 50, true).await.is_err());
        assert_eq!(fs::read(&source).unwrap(), content);
    }
    assert!(
        processor
            .prepare(Path::new("relative.pdf"), 50, true)
            .await
            .is_err()
    );
    for invalid in [
        temporary.path().join("missing"),
        temporary.path().join("source.txt"),
    ] {
        assert!(matches!(
            processor.prepare(&invalid, 50, true).await,
            Err(PdfError::InvalidInput)
        ));
    }
    let large = File::create(&source).unwrap();
    large.set_len(MAX_DOCUMENT_BYTES + 1).unwrap();
    assert!(processor.prepare(&source, 50, true).await.is_err());
    #[cfg(unix)]
    {
        let link = temporary.path().join("link.pdf");
        std::os::unix::fs::symlink(&source, &link).unwrap();
        assert!(processor.prepare(&link, 50, true).await.is_err());
    }
    for bytes in [
        br"{}".as_slice(),
        br#"{"version":1,"pages":[],"outlines":[]}"#,
        br#"{"version":2,"pages":[{"pageposfrom1":2}],"outlines":[]}"#,
    ] {
        assert!(parse_structure(bytes).is_err());
    }
    let structure = parse_structure(br#"{"version":2,"pages":[{"pageposfrom1":1}],"outlines":[{"destpageposfrom1":null,"title":"Section","kids":[{"destpageposfrom1":1,"title":"Cells","kids":[]}]}]}"#).unwrap();
    assert_eq!(
        structure.chapters().unwrap(),
        vec![Chapter {
            pages: 0..1,
            title: "Cells".into()
        }]
    );
}

#[tokio::test]
async fn uses_the_configured_chunk_size_and_overlap_on_real_documents() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("custom.pdf");
    let original = include_bytes!("../../tests/fixtures/chapters.pdf");
    fs::write(&source, original).unwrap();
    let processor = PdfProcessor::new(PdfChunkPlanner::new(17, 2).unwrap());
    let prepared = processor.prepare(&source, 17, false).await.unwrap();
    assert_eq!(prepared.total_pages(), 70);
    assert_eq!(prepared.files().len(), 5);
    for (path, expected) in prepared
        .files()
        .iter()
        .zip([0..17, 15..32, 30..47, 45..62, 60..70])
    {
        assert_eq!(page_widths(path).await, expected.collect::<Vec<_>>());
    }
    let staged = prepared.directory().to_owned();
    drop(prepared);
    assert!(!staged.exists());
    assert_eq!(fs::read(source).unwrap(), original);
}

#[tokio::test]
async fn recovers_cross_reference_warnings_without_modifying_the_source() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("recoverable.pdf");
    let original = include_bytes!("../../tests/fixtures/chapters.pdf");
    let marker = original
        .windows(b"startxref".len())
        .rposition(|bytes| bytes == b"startxref")
        .unwrap();
    let mut damaged = original[..marker].to_vec();
    damaged.extend_from_slice(b"startxref\n0\n%%EOF\n");
    fs::write(&source, &damaged).unwrap();
    let check = run_bounded(
        Command::new("qpdf").arg("--check").arg(&source),
        Duration::from_secs(30),
        65_536,
    )
    .await
    .unwrap();
    assert_eq!(check.status.code(), Some(3));
    let prepared = PdfProcessor::default()
        .prepare(&source, 50, false)
        .await
        .unwrap();
    assert_eq!(prepared.total_pages(), 70);
    for (path, expected) in prepared.files().iter().zip([0..30, 25..55, 50..70]) {
        assert_eq!(page_widths(path).await, expected.collect::<Vec<_>>());
    }
    assert_eq!(fs::read(&source).unwrap(), damaged);
}

#[tokio::test(start_paused = true)]
async fn enforces_the_overall_preparation_deadline_without_changing_the_source() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("source.pdf");
    let original = include_bytes!("../../tests/fixtures/chapters.pdf");
    fs::write(&source, original).unwrap();
    let processor = PdfProcessor::default();
    let preparation = processor.prepare(&source, 50, false);
    tokio::pin!(preparation);
    let mut context = std::task::Context::from_waker(std::task::Waker::noop());
    assert!(std::future::Future::poll(preparation.as_mut(), &mut context).is_pending());
    tokio::time::advance(Duration::from_secs(601)).await;
    assert!(matches!(preparation.await, Err(PdfError::Timeout)));
    assert_eq!(fs::read(&source).unwrap(), original);
}

#[tokio::test]
async fn validates_empty_chunks_page_ranges_and_the_aggregate_output_budget() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("source.pdf");
    let output = directory.path().join("chunk.pdf");
    let original = include_bytes!("../../tests/fixtures/chapters.pdf");
    fs::write(&source, original).unwrap();
    let mut bytes = 0;
    write_chunk(&source, &output, &PdfChunk::default(), &mut bytes)
        .await
        .unwrap();
    assert!(bytes > 0);
    assert_eq!(page_widths(&output).await, Vec::<usize>::new());
    let invalid = PdfChunk {
        pages: std::iter::once(1000..1001).collect(),
        titles: Vec::new(),
    };
    assert!(matches!(
        write_chunk(&source, &output, &invalid, &mut bytes).await,
        Err(PdfError::InvalidStructure)
    ));
    let chunk = PdfChunk {
        pages: std::iter::once(0..1).collect(),
        titles: Vec::new(),
    };
    bytes = 0;
    write_chunk(&source, &output, &chunk, &mut bytes)
        .await
        .unwrap();
    let size = bytes;
    bytes = 4 * MAX_DOCUMENT_BYTES - size;
    write_chunk(&source, &output, &chunk, &mut bytes)
        .await
        .unwrap();
    assert_eq!(bytes, 4 * MAX_DOCUMENT_BYTES);
    bytes = 4 * MAX_DOCUMENT_BYTES - size + 1;
    assert!(matches!(
        write_chunk(&source, &output, &chunk, &mut bytes).await,
        Err(PdfError::InvalidStructure)
    ));
    assert_eq!(bytes, 4 * MAX_DOCUMENT_BYTES + 1);
    assert_eq!(fs::read(source).unwrap(), original);
}
