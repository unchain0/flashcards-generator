use super::*;
use serde_json::{Value, json};
use std::path::PathBuf;

struct Fixture {
    directory: tempfile::TempDir,
    metadata: PathBuf,
    coverage: PathBuf,
    probe: PathBuf,
    snapshot: PathBuf,
    source: PathBuf,
    report: Value,
}

fn fixture() -> CheckResult<Fixture> {
    let directory = tempfile::tempdir()?;
    let mut packages = Vec::new();
    for name in [
        "domain",
        "engines",
        "services",
        "integrations",
        "delivery",
        "xtask",
        "integrations-shared",
        "integrations-server",
        "delivery-shared",
        "delivery-server",
    ] {
        let crate_dir = directory.path().join("rust").join(name);
        std::fs::create_dir_all(crate_dir.join("src"))?;
        let manifest = crate_dir.join("Cargo.toml");
        std::fs::write(&manifest, "[lints]\nworkspace = true\n")?;
        let package = if name == "xtask" {
            name.to_owned()
        } else {
            format!("flashcards-{name}")
        };
        packages.push(json!({"id": package, "name": package, "source": null,
            "manifest_path": manifest, "dependencies": []}));
    }
    let ids = packages
        .iter()
        .map(|package| package["id"].clone())
        .collect::<Vec<_>>();
    let nodes = ids
        .iter()
        .map(|id| json!({"id": id, "deps": []}))
        .collect::<Vec<_>>();
    let metadata = directory.path().join("metadata.json");
    std::fs::write(
        &metadata,
        serde_json::to_vec(&json!({"version": 1,
        "workspace_root": directory.path(), "workspace_members": ids,
        "packages": packages, "resolve": {"nodes": nodes}}))?,
    )?;
    let source = directory.path().join("rust/domain/src/lib.rs");
    std::fs::write(
        &source,
        "fn production(value: bool) -> u8 { if value { 1 } else { 0 } }",
    )?;
    let counter = json!({"count": 1, "covered": 1});
    let report = json!({"type": "llvm.coverage.json.export", "version": "3.1.0",
        "data": [{"functions": [], "files": [{"filename": source,
            "summary": {"lines": counter, "functions": counter,
                "branches": {"count": 2, "covered": 2}}}]}]});
    let coverage = directory.path().join("coverage.json");
    std::fs::write(&coverage, serde_json::to_vec(&report)?)?;
    let probe = directory.path().join("probe.json");
    std::fs::write(
        &probe,
        serde_json::to_vec(&json!({"type": "llvm.coverage.json.export",
        "version": "3.1.0", "data": [{"files": [], "functions": [{
            "name": "coverage_probe::branch_if", "branches": [[1, 1, 1, 9, 1, 1, 0, 0, 4]]}]}]}))?,
    )?;
    let snapshot = directory.path().join("snapshot.json");
    coverage_evidence::capture(&metadata, &snapshot)?;
    coverage_evidence::seal(&metadata, &coverage, &probe, &snapshot)?;
    Ok(Fixture {
        directory,
        metadata,
        coverage,
        probe,
        snapshot,
        source,
        report,
    })
}

#[test]
fn changed_source_cannot_reuse_an_otherwise_complete_report() -> CheckResult<()> {
    let fixture = fixture()?;
    check_collected(
        &fixture.metadata,
        &fixture.coverage,
        &fixture.probe,
        &fixture.snapshot,
    )?;
    std::fs::write(
        &fixture.source,
        "fn production(value: bool) -> u8 { if value { 1 } else { 0 } }\nfn untested() -> u8 { 2 }",
    )?;
    let error = check_collected(
        &fixture.metadata,
        &fixture.coverage,
        &fixture.probe,
        &fixture.snapshot,
    )
    .unwrap_err();
    assert!(error.to_string().contains("Coverage inputs changed"));
    Ok(())
}

#[test]
fn changed_test_inputs_cannot_reuse_or_seal_collected_reports() -> CheckResult<()> {
    for relative in [
        "rust/domain/src/tests.rs",
        "rust/integrations/tests/execution_identity.rs",
        "rust/integrations/tests/fixtures/source.pdf",
        "rust/xtask/src/checker.rs",
        "frontend/src/controller.test.ts",
        "frontend/e2e/generation.spec.ts",
        "frontend/e2e/fixtures/source.pptx",
    ] {
        let fixture = fixture()?;
        let input = fixture.directory.path().join(relative);
        std::fs::create_dir_all(input.parent().unwrap())?;
        if relative == "rust/domain/src/tests.rs" {
            let source = std::fs::read_to_string(&fixture.source)?;
            std::fs::write(
                &fixture.source,
                format!("{source}\n#[cfg(test)] mod tests;"),
            )?;
        }
        std::fs::write(&input, "fn initial_test() {}")?;
        let sealed = fixture.snapshot.with_file_name("tests-sealed.json");
        let unsealed = fixture.snapshot.with_file_name("tests-unsealed.json");
        coverage_evidence::capture(&fixture.metadata, &sealed)?;
        coverage_evidence::seal(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &sealed,
        )?;
        coverage_evidence::capture(&fixture.metadata, &unsealed)?;
        std::fs::write(&input, "fn changed_test() {}")?;
        let error = check_collected(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &sealed,
        )
        .unwrap_err();
        assert!(
            error.to_string().contains("Coverage inputs changed"),
            "{relative}: {error}"
        );
        let error = coverage_evidence::seal(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &unsealed,
        )
        .unwrap_err();
        assert!(
            error.to_string().contains("Coverage inputs changed"),
            "{relative}: {error}"
        );
    }
    Ok(())
}

#[test]
fn added_or_removed_test_inputs_invalidate_collected_reports() -> CheckResult<()> {
    let inputs = [
        "rust/integrations/tests/contract.rs",
        "frontend/e2e/fixtures/document.pdf",
        "frontend/src/controller.test.ts",
        "scripts/test-helper.sh",
    ];
    for (relative, remove) in inputs
        .into_iter()
        .flat_map(|path| [(path, false), (path, true)])
    {
        let fixture = fixture()?;
        let input = fixture.directory.path().join(relative);
        std::fs::create_dir_all(input.parent().unwrap())?;
        if remove {
            std::fs::write(&input, "initial test input")?;
        }
        let snapshot = fixture.snapshot.with_file_name("test-inventory.json");
        coverage_evidence::capture(&fixture.metadata, &snapshot)?;
        coverage_evidence::seal(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &snapshot,
        )?;
        if remove {
            std::fs::remove_file(&input)?;
        } else {
            std::fs::write(&input, "new test input")?;
        }
        let error = check_collected(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &snapshot,
        )
        .unwrap_err();
        assert!(
            error.to_string().contains("Coverage inputs changed"),
            "{relative}: {error}"
        );
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn snapshots_reject_symlinked_test_files_and_directories() -> CheckResult<()> {
    for directory in [false, true] {
        let fixture = fixture()?;
        let parent = fixture.directory.path().join("frontend/e2e");
        std::fs::create_dir_all(&parent)?;
        let target = if directory {
            fixture.directory.path()
        } else {
            &fixture.source
        };
        std::os::unix::fs::symlink(target, parent.join("aliased-test"))?;
        let error = coverage_evidence::capture(
            &fixture.metadata,
            &fixture.snapshot.with_file_name("test-alias.json"),
        )
        .unwrap_err();
        assert!(error.to_string().contains("regular files"), "{error}");
    }
    Ok(())
}

#[test]
fn collected_reports_reject_changed_artifacts_and_metadata() -> CheckResult<()> {
    for (input, expected) in [
        ("coverage", "Coverage reports changed"),
        ("probe", "Coverage reports changed"),
        ("metadata", "Coverage metadata changed"),
    ] {
        let fixture = fixture()?;
        let path = match input {
            "coverage" => &fixture.coverage,
            "probe" => &fixture.probe,
            _ => &fixture.metadata,
        };
        let mut bytes = std::fs::read(path)?;
        bytes.push(b' ');
        std::fs::write(path, bytes)?;
        let error = check_collected(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &fixture.snapshot,
        )
        .unwrap_err();
        assert!(error.to_string().contains(expected), "{error}");
    }
    Ok(())
}

#[test]
fn collection_cannot_be_sealed_after_sources_change() -> CheckResult<()> {
    let fixture = fixture()?;
    let snapshot = fixture.snapshot.with_file_name("unsealed.json");
    coverage_evidence::capture(&fixture.metadata, &snapshot)?;
    let error = check_collected(
        &fixture.metadata,
        &fixture.coverage,
        &fixture.probe,
        &snapshot,
    )
    .unwrap_err();
    assert!(error.to_string().contains("not sealed"));
    std::fs::write(&fixture.source, "fn changed() {}")?;
    let error = coverage_evidence::seal(
        &fixture.metadata,
        &fixture.coverage,
        &fixture.probe,
        &snapshot,
    )
    .unwrap_err();
    assert!(error.to_string().contains("Coverage inputs changed"));
    Ok(())
}

#[test]
fn collected_reports_reject_added_sources_and_changed_configuration() -> CheckResult<()> {
    for path in [
        "rust/domain/src/added.rs",
        "Cargo.lock",
        "rust/domain/Cargo.toml",
        "rust/engines/src/quality_stopwords.txt",
        "rust/integrations-server/migrations/0001_initial.sql",
    ] {
        let fixture = fixture()?;
        let path = fixture.directory.path().join(path);
        std::fs::create_dir_all(path.parent().unwrap())?;
        let mut bytes = std::fs::read(&path).unwrap_or_default();
        bytes.extend_from_slice(b"\n");
        std::fs::write(path, bytes)?;
        let error = check_collected(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &fixture.snapshot,
        )
        .unwrap_err();
        assert!(
            error.to_string().contains("Coverage inputs changed"),
            "{error}"
        );
    }
    Ok(())
}

#[test]
fn changed_embedded_resources_cannot_reuse_or_seal_collected_reports() -> CheckResult<()> {
    for operation in ["replace", "remove", "add", "stopwords"] {
        let fixture = fixture()?;
        let directory = fixture
            .directory
            .path()
            .join("rust/integrations-server/migrations");
        std::fs::create_dir_all(&directory)?;
        let migration = directory.join("0001_initial.sql");
        std::fs::write(&migration, "CREATE TABLE example (id INTEGER);")?;
        let stopwords = fixture
            .directory
            .path()
            .join("rust/engines/src/quality_stopwords.txt");
        std::fs::write(&stopwords, "example")?;
        let sealed = fixture.snapshot.with_file_name("resources-sealed.json");
        let unsealed = fixture.snapshot.with_file_name("resources-unsealed.json");
        coverage_evidence::capture(&fixture.metadata, &sealed)?;
        coverage_evidence::seal(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &sealed,
        )?;
        coverage_evidence::capture(&fixture.metadata, &unsealed)?;
        check_collected(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &sealed,
        )?;
        match operation {
            "replace" => std::fs::write(&migration, "CREATE TABLE changed (id INTEGER);")?,
            "remove" => std::fs::remove_file(&migration)?,
            "add" => std::fs::write(
                directory.join("0002_added.sql"),
                "ALTER TABLE example ADD value TEXT;",
            )?,
            _ => std::fs::write(&stopwords, "changed")?,
        }
        let error = check_collected(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &sealed,
        )
        .unwrap_err();
        assert!(
            error.to_string().contains("Coverage inputs changed"),
            "{error}"
        );
        let error = coverage_evidence::seal(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &unsealed,
        )
        .unwrap_err();
        assert!(
            error.to_string().contains("Coverage inputs changed"),
            "{error}"
        );
    }
    Ok(())
}

#[test]
fn snapshots_cannot_be_overwritten_resealed_or_silently_omitted() -> CheckResult<()> {
    let fixture = fixture()?;
    assert!(coverage_evidence::capture(&fixture.metadata, &fixture.snapshot).is_err());
    let error = coverage_evidence::seal(
        &fixture.metadata,
        &fixture.coverage,
        &fixture.probe,
        &fixture.snapshot,
    )
    .unwrap_err();
    assert!(error.to_string().contains("already sealed"));
    std::fs::remove_file(&fixture.snapshot)?;
    assert!(
        check_collected(
            &fixture.metadata,
            &fixture.coverage,
            &fixture.probe,
            &fixture.snapshot,
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn snapshots_reject_unknown_schemas_and_oversized_input() -> CheckResult<()> {
    let fixture = fixture()?;
    let original: Value = serde_json::from_slice(&std::fs::read(&fixture.snapshot)?)?;
    for (key, value) in [("version", json!(2)), ("unexpected", json!(true))] {
        let mut changed = original.clone();
        changed[key] = value;
        std::fs::write(&fixture.snapshot, serde_json::to_vec(&changed)?)?;
        assert!(
            check_collected(
                &fixture.metadata,
                &fixture.coverage,
                &fixture.probe,
                &fixture.snapshot,
            )
            .is_err()
        );
    }
    std::fs::write(&fixture.snapshot, vec![b' '; 1024 * 1024 + 1])?;
    let error = check_collected(
        &fixture.metadata,
        &fixture.coverage,
        &fixture.probe,
        &fixture.snapshot,
    )
    .unwrap_err();
    assert!(error.to_string().contains("size limit"));
    Ok(())
}

#[cfg(unix)]
#[test]
fn snapshots_reject_symlinked_migration_directories() -> CheckResult<()> {
    let fixture = fixture()?;
    let outside = tempfile::tempdir()?;
    std::fs::write(outside.path().join("0001_initial.sql"), "SELECT 1;")?;
    std::os::unix::fs::symlink(
        outside.path(),
        fixture
            .directory
            .path()
            .join("rust/integrations-server/migrations"),
    )?;
    assert!(
        coverage_evidence::capture(
            &fixture.metadata,
            &fixture.snapshot.with_file_name("migration-alias.json")
        )
        .is_err()
    );
    Ok(())
}

#[cfg(unix)]
#[test]
fn snapshots_reject_symlinked_evidence_and_configuration() -> CheckResult<()> {
    let fixture = fixture()?;
    let alias = fixture.snapshot.with_file_name("alias.json");
    std::os::unix::fs::symlink(&fixture.snapshot, &alias)?;
    let error =
        check_collected(&fixture.metadata, &fixture.coverage, &fixture.probe, &alias).unwrap_err();
    assert!(error.to_string().contains("regular file"));
    let configuration = fixture.directory.path().join("Cargo.lock");
    std::os::unix::fs::symlink(&fixture.source, configuration)?;
    let error = coverage_evidence::capture(
        &fixture.metadata,
        &fixture.snapshot.with_file_name("new.json"),
    )
    .unwrap_err();
    assert!(error.to_string().contains("regular files"));
    Ok(())
}

#[test]
fn missing_unimported_executable_sources_block_otherwise_complete_coverage() -> CheckResult<()> {
    let fixture = fixture()?;
    check(&fixture.metadata, &fixture.coverage, &fixture.probe)?;
    std::fs::write(
        fixture.source.with_file_name("unimported.rs"),
        "fn missing() {}",
    )?;
    let error = check(&fixture.metadata, &fixture.coverage, &fixture.probe).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("1 executable source files absent")
    );
    assert!(error.to_string().contains("unimported.rs"));
    Ok(())
}

#[test]
fn duplicate_production_files_and_zero_branch_reports_fail() -> CheckResult<()> {
    let mut fixture = fixture()?;
    let duplicate = fixture.report["data"][0]["files"][0].clone();
    fixture.report["data"][0]["files"]
        .as_array_mut()
        .unwrap()
        .push(duplicate);
    std::fs::write(&fixture.coverage, serde_json::to_vec(&fixture.report)?)?;
    let error = check(&fixture.metadata, &fixture.coverage, &fixture.probe).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("Duplicate production coverage file")
    );
    fixture.report["data"][0]["files"]
        .as_array_mut()
        .unwrap()
        .pop();
    fixture.report["data"][0]["files"][0]["summary"]["branches"] =
        json!({"count": 0, "covered": 0});
    std::fs::write(&fixture.coverage, serde_json::to_vec(&fixture.report)?)?;
    let error = check(&fixture.metadata, &fixture.coverage, &fixture.probe).unwrap_err();
    assert!(error.to_string().contains("No production branch counters"));
    Ok(())
}

#[test]
fn instrumented_totals_do_not_approve_unmeasured_production_constructions() -> CheckResult<()> {
    let fixture = fixture()?;
    std::fs::write(
        &fixture.source,
        "fn production(value: bool) -> u8 { match value { true => 1, false => 0 } }",
    )?;
    let error = check(&fixture.metadata, &fixture.coverage, &fixture.probe).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("Production branch instrumentation not demonstrated: branch_match")
    );
    Ok(())
}
