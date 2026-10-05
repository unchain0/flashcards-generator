use super::*;

#[test]
fn inventories_pattern_and_loop_decisions_without_test_bodies() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("lib.rs");
    std::fs::write(
        &path,
        "fn production(values: &[u8]) {
            if let Some(value) = values.first() { consume(value); }
            for value in values { consume(value); }
            while ready() { consume(&0); }
            while let Some(value) = next() { consume(value); }
        }",
    )?;
    let source = parse(&path)?;
    for probe in [
        "branch_if_let",
        "branch_for",
        "branch_while",
        "branch_while_let",
    ] {
        assert!(source.constructions.contains(probe), "Missing {probe}");
    }
    std::fs::write(
        &path,
        "#[cfg(test)] fn helper() { for value in values { consume(value); } }",
    )?;
    assert!(parse(&path)?.constructions.is_empty());
    Ok(())
}

#[test]
fn inventory_includes_unimported_production_sources_and_skips_test_helpers() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    std::fs::write(
        directory.path().join("unused.rs"),
        "fn unused() { if true {} }",
    )?;
    std::fs::write(directory.path().join("tests.rs"), "invalid rust")?;
    std::fs::write(directory.path().join("lib.rs"), "#[cfg(test)] mod tests;")?;
    std::fs::create_dir(directory.path().join("tests"))?;
    std::fs::write(directory.path().join("tests/helper.rs"), "invalid rust")?;
    let sources = inventory(&[directory.path().to_owned()])?;
    assert_eq!(sources.len(), 2);
    let source = sources.get(&directory.path().join("unused.rs")).unwrap();
    assert!(source.executable);
    assert!(source.constructions.contains("branch_if"));
    Ok(())
}

#[test]
fn test_names_do_not_exclude_unclassified_production_sources() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    for name in ["tests.rs", "hidden_tests.rs"] {
        std::fs::write(
            directory.path().join(name),
            "fn production() { if true {} }",
        )?;
    }
    std::fs::create_dir(directory.path().join("tests"))?;
    std::fs::write(
        directory.path().join("tests/helper.rs"),
        "fn production() {}",
    )?;
    std::fs::write(directory.path().join("lib.rs"), "mod tests;")?;
    let sources = inventory(&[directory.path().to_owned()])?;
    assert_eq!(sources.len(), 4);
    assert_eq!(
        sources.values().filter(|source| source.executable).count(),
        3
    );
    Ok(())
}

#[test]
fn test_only_bodies_do_not_hide_or_add_production_decisions() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("lib.rs");
    std::fs::write(
        &path,
        "fn production() {} #[cfg(test)] mod tests { fn helper() { match 1 { _ => () } } }",
    )?;
    let source = parse(&path)?;
    assert!(source.executable);
    assert!(source.constructions.is_empty());
    std::fs::write(&path, "#[cfg(test)] fn test_helper() { if true {} }")?;
    assert!(!parse(&path)?.executable);
    Ok(())
}

#[test]
fn conditional_test_guards_must_require_test_in_every_enabled_case() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("lib.rs");
    for predicate in ["all(test, unix)", "any(test, all(test, feature = \"qa\"))"] {
        std::fs::write(
            &path,
            format!("#[cfg({predicate})] fn helper() {{ if true {{}} }}"),
        )?;
        assert!(!parse(&path)?.executable);
    }
    for predicate in [
        "any(test, unix)",
        "not(test)",
        "all(unix, feature = \"qa\")",
    ] {
        std::fs::write(
            &path,
            format!("#[cfg({predicate})] fn production() {{ if true {{}} }}"),
        )?;
        assert!(parse(&path)?.executable);
    }
    std::fs::write(&path, "#[cfg(all(test, unix))] mod tests;")?;
    std::fs::write(directory.path().join("tests.rs"), "invalid rust")?;
    assert_eq!(inventory(&[directory.path().to_owned()])?.len(), 1);
    Ok(())
}

#[test]
fn coverage_attributes_are_rejected_on_production_only() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("lib.rs");
    for attribute in [
        "coverage(off)",
        "cfg(coverage)",
        "cfg_attr(coverage_nightly, coverage(off))",
    ] {
        std::fs::write(&path, format!("#[{attribute}] fn production() {{}}"))?;
        assert!(parse(&path).is_err());
    }
    std::fs::write(
        &path,
        "#[cfg(test)] mod tests { #[coverage(off)] fn helper() {} }",
    )?;
    assert!(parse(&path).is_ok());
    Ok(())
}

#[cfg(unix)]
#[test]
fn inventory_rejects_symlinks() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    let external = tempfile::NamedTempFile::new()?;
    std::os::unix::fs::symlink(external.path(), directory.path().join("hidden.rs"))?;
    assert!(inventory(&[directory.path().to_owned()]).is_err());
    Ok(())
}
