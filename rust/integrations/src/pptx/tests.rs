use super::*;

#[cfg(unix)]
#[tokio::test]
async fn conversion_reports_a_readonly_destination_without_changing_originals_or_permissions() {
    use std::os::unix::fs::PermissionsExt;
    let (directory, converter) = publication_failure_fixture(true);
    let parent = directory.path().join("destination");
    let source = directory.path().join("source.pptx");
    let output = parent.join("output.pdf");
    let result = converter.convert(&source, &output).await;
    let mode = fs::metadata(&parent).unwrap().permissions().mode() & 0o777;
    fs::set_permissions(&parent, fs::Permissions::from_mode(0o700)).unwrap();
    let error = result.unwrap_err();
    assert!(!error.to_string().contains("destination"));
    let ConversionError::Io(cause) = error else {
        panic!("Publication failures must retain their I/O cause: {error:?}");
    };
    assert_eq!(cause.kind(), io::ErrorKind::PermissionDenied);
    assert_eq!(mode, 0o500);
    assert_eq!(fs::read(&output).unwrap(), b"preserve previous PDF");
    assert_eq!(
        fs::read(&source).unwrap(),
        include_bytes!("../../tests/fixtures/minimal.pptx")
    );
    let entries = fs::read_dir(&parent)
        .unwrap()
        .map(Result::unwrap)
        .collect::<Vec<_>>();
    assert!(
        entries
            .iter()
            .all(|entry| !entry.file_name().to_string_lossy().starts_with(".pdf-"))
    );
    assert!(
        entries
            .iter()
            .filter(|entry| entry.file_name().to_string_lossy().starts_with(".pptx-"))
            .all(|entry| fs::read_dir(entry.path()).unwrap().count() == 0)
    );
}

#[cfg(unix)]
#[tokio::test]
async fn conversion_commit_failure_preserves_a_replaced_destination_and_removes_temporaries() {
    let (directory, converter) = publication_failure_fixture(false);
    let parent = directory.path().join("destination");
    let source = directory.path().join("source.pptx");
    let output = parent.join("output.pdf");
    let error = converter.convert(&source, &output).await.unwrap_err();
    assert!(!error.to_string().contains("destination"));
    let ConversionError::Io(cause) = error else {
        panic!("Commit failures must retain their I/O cause: {error:?}");
    };
    assert_eq!(cause.kind(), io::ErrorKind::IsADirectory);
    assert_eq!(
        fs::read(parent.join("previous.pdf")).unwrap(),
        b"preserve previous PDF"
    );
    assert_eq!(
        fs::read(output.join("marker")).unwrap(),
        b"preserve replacement"
    );
    assert_eq!(
        fs::read(&source).unwrap(),
        include_bytes!("../../tests/fixtures/minimal.pptx")
    );
    assert_eq!(fs::read_dir(&parent).unwrap().count(), 2);
}

#[cfg(unix)]
fn publication_failure_fixture(readonly: bool) -> (tempfile::TempDir, PptxConverter) {
    use std::os::unix::fs::PermissionsExt;
    let directory = tempfile::tempdir().unwrap();
    let parent = directory.path().join("destination");
    fs::create_dir(&parent).unwrap();
    fs::write(parent.join("output.pdf"), b"preserve previous PDF").unwrap();
    fs::write(
        directory.path().join("source.pptx"),
        include_bytes!("../../tests/fixtures/minimal.pptx"),
    )
    .unwrap();
    fs::write(
        directory.path().join("publication-template.pdf"),
        include_bytes!("../../tests/fixtures/chapters.pdf"),
    )
    .unwrap();
    if readonly {
        fs::write(parent.join("readonly-case"), b"").unwrap();
    }
    let executable = directory.path().join("controlled-converter");
    fs::write(
        &executable,
        br#"#!/bin/sh
set -eu
source_file=''
for argument in "$@"; do source_file="$argument"; done
staging_directory="${source_file%/*}"
destination_directory="${staging_directory%/*}"
template_file="${destination_directory%/*}/publication-template.pdf"
cp "$template_file" "$staging_directory/source.pdf"
if [ -f "$destination_directory/readonly-case" ]; then
    chmod 500 "$destination_directory"
else
    mv "$destination_directory/output.pdf" "$destination_directory/previous.pdf"
    mkdir "$destination_directory/output.pdf"
    printf 'preserve replacement' > "$destination_directory/output.pdf/marker"
fi
"#,
    )
    .unwrap();
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
    (directory, PptxConverter::new(executable))
}

#[tokio::test]
async fn conversion_diagnostics_preserve_causes_without_disclosing_document_paths() {
    let worker = tokio::spawn(std::future::pending::<()>());
    worker.abort();
    let errors = [
        ConversionError::InvalidInput,
        ConversionError::InvalidOutput,
        ConversionError::from(io::Error::other("synthetic-private-presentation-path")),
        ConversionError::Process(ProcessError::Io(io::Error::other(
            "synthetic-private-presentation-path",
        ))),
        ConversionError::Worker(worker.await.unwrap_err()),
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), index >= 2);
        assert!(
            !error
                .to_string()
                .contains("synthetic-private-presentation-path")
        );
    }
    assert_eq!(
        errors[2].source().unwrap().to_string(),
        "synthetic-private-presentation-path"
    );
    assert_eq!(
        errors[3].source().unwrap().source().unwrap().to_string(),
        "synthetic-private-presentation-path"
    );
}

#[cfg(unix)]
#[tokio::test]
async fn rejects_corrupt_converter_output_after_a_successful_process_exit() {
    use std::os::unix::fs::PermissionsExt;
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("source.pptx");
    let output = directory.path().join("previous.pdf");
    let executable = directory.path().join("controlled-converter");
    let original = include_bytes!("../../tests/fixtures/minimal.pptx");
    fs::write(&source, original).unwrap();
    fs::write(&output, b"preserve previous PDF").unwrap();
    fs::write(
        &executable,
        br#"#!/bin/sh
set -eu
source_file=''
for argument in "$@"; do source_file="$argument"; done
printf '%%PDF-1.7\ncorrupt output\n' > "${source_file%/*}/source.pdf"
"#,
    )
    .unwrap();
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
    assert!(matches!(
        PptxConverter::new(executable)
            .convert(&source, &output)
            .await,
        Err(ConversionError::InvalidOutput)
    ));
    assert_eq!(fs::read(&source).unwrap(), original);
    assert_eq!(fs::read(&output).unwrap(), b"preserve previous PDF");
    assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 3);
}

#[test]
fn rejects_invalid_converted_files_and_preserves_existing_outputs() {
    let directory = tempfile::tempdir().unwrap();
    let converted = directory.path().join("converted.pdf");
    let output = directory.path().join("previous.pdf");
    fs::write(&output, b"preserve previous export").unwrap();
    for content in [b"".as_slice(), b"x", b"invalid output"] {
        fs::write(&converted, content).unwrap();
        assert!(publish(&converted, &output).is_err());
        assert_eq!(fs::read(&output).unwrap(), b"preserve previous export");
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 2);
    }
    File::options()
        .write(true)
        .open(&converted)
        .unwrap()
        .set_len(MAX_DOCUMENT_BYTES + 1)
        .unwrap();
    assert!(matches!(
        publish(&converted, &output),
        Err(ConversionError::InvalidOutput)
    ));
    assert_eq!(fs::read(&output).unwrap(), b"preserve previous export");
}

#[tokio::test]
async fn converts_a_real_presentation_with_an_isolated_profile_and_preserves_originals() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("Presentation with spaces.PPTX");
    let original = include_bytes!("../../tests/fixtures/minimal.pptx");
    fs::write(&source, original).unwrap();
    let output = temporary.path().join("presentation.pdf");
    fs::write(&output, "previous export").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let staged = prepare(&source, &output).unwrap();
        assert_eq!(
            fs::metadata(staged.path()).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(staged.path().join("source.pptx"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    assert_eq!(
        PptxConverter::default()
            .convert(&source, &output)
            .await
            .unwrap(),
        output
    );
    assert!(fs::read(&output).unwrap().starts_with(b"%PDF-"));
    assert_eq!(fs::read(&source).unwrap(), original);
    let verified = run_bounded(
        Command::new("qpdf").arg("--check").arg(&output),
        Duration::from_secs(30),
        65_536,
    )
    .await
    .unwrap();
    assert!(verified.status.success());
    assert_eq!(fs::read_dir(temporary.path()).unwrap().count(), 2);
    let current = fs::read(&output).unwrap();
    let invalid = temporary.path().join("invalid.pptx");
    fs::write(&invalid, b"not a presentation").unwrap();
    assert!(
        PptxConverter::default()
            .convert(&invalid, &output)
            .await
            .is_err()
    );
    assert_eq!(fs::read(&output).unwrap(), current);
    assert_eq!(fs::read_dir(temporary.path()).unwrap().count(), 3);
    fs::write(&invalid, b"PK\x03\x04invalid ZIP contents").unwrap();
    assert!(
        PptxConverter::default()
            .convert(&invalid, &output)
            .await
            .is_err()
    );
    assert_eq!(fs::read(&output).unwrap(), current);
    assert_eq!(fs::read_dir(temporary.path()).unwrap().count(), 3);
    assert!(
        PptxConverter::new(temporary.path().join("missing-soffice"))
            .convert(&source, &output)
            .await
            .is_err()
    );
    assert_eq!(fs::read(&output).unwrap(), current);
}

#[tokio::test]
async fn rejects_unsafe_sources_and_outputs_without_modifying_existing_files() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("source.pptx");
    let output = temporary.path().join("output.pdf");
    fs::write(&source, b"source").unwrap();
    fs::write(&output, b"preserve").unwrap();
    for path in [
        Path::new("relative.pptx").to_owned(),
        temporary.path().join("missing.pptx"),
        temporary.path().join("source.txt"),
    ] {
        assert!(
            PptxConverter::default()
                .convert(&path, &output)
                .await
                .is_err()
        );
    }
    fs::write(&source, b"").unwrap();
    assert!(
        PptxConverter::default()
            .convert(&source, &output)
            .await
            .is_err()
    );
    File::options()
        .write(true)
        .open(&source)
        .unwrap()
        .set_len(MAX_DOCUMENT_BYTES + 1)
        .unwrap();
    assert!(
        PptxConverter::default()
            .convert(&source, &output)
            .await
            .is_err()
    );
    #[cfg(unix)]
    {
        let alias = temporary.path().join("alias.pptx");
        std::os::unix::fs::symlink(&source, &alias).unwrap();
        assert!(
            PptxConverter::default()
                .convert(&alias, &output)
                .await
                .is_err()
        );
    }
    assert_eq!(fs::read(output).unwrap(), b"preserve");
    assert!(!fs::read_dir(temporary.path()).unwrap().any(|entry| {
        entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".pptx-")
    }));
}

#[test]
fn rejects_truncated_presentations_and_invalid_destination_parents_before_staging() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("source.pptx");
    let output = directory.path().join("output.pdf");
    fs::write(&output, b"previous export").unwrap();
    for header in [b"P".as_slice(), b"PK", b"PK\x03"] {
        fs::write(&source, header).unwrap();
        assert!(matches!(
            prepare(&source, &output),
            Err(ConversionError::InvalidInput)
        ));
        assert_eq!(fs::read(&source).unwrap(), header);
        assert_eq!(fs::read(&output).unwrap(), b"previous export");
    }
    fs::write(&source, include_bytes!("../../tests/fixtures/minimal.pptx")).unwrap();
    for destination in [
        PathBuf::from("relative.pdf"),
        directory.path().join("output.txt"),
    ] {
        assert!(matches!(
            prepare(&source, &destination),
            Err(ConversionError::InvalidInput)
        ));
    }
    assert!(matches!(
        prepare(&source, &output.join("nested.pdf")),
        Err(ConversionError::InvalidInput)
    ));
    assert!(
        matches!(prepare(&source, &directory.path().join("missing/output.pdf")), Err(ConversionError::Io(error)) if error.kind() == io::ErrorKind::NotFound)
    );
    #[cfg(unix)]
    {
        let alias = directory.path().join("alias");
        std::os::unix::fs::symlink(directory.path(), &alias).unwrap();
        assert!(matches!(
            prepare(&source, &alias.join("output.pdf")),
            Err(ConversionError::InvalidInput)
        ));
    }
    assert_eq!(fs::read(&output).unwrap(), b"previous export");
    assert!(fs::read_dir(directory.path()).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".pptx-")
    }));
}
