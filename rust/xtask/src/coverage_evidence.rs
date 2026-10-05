use crate::{CheckResult, metadata, syntax};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{ErrorKind, Write},
    path::{Path, PathBuf},
};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u64,
    metadata: String,
    inputs: BTreeMap<PathBuf, Option<String>>,
    artifacts: Option<[String; 2]>,
}

pub(super) fn capture(metadata: &Path, output: &Path) -> CheckResult<()> {
    let snapshot = Snapshot {
        version: 1,
        metadata: digest(metadata, 64 * 1024 * 1024)?,
        inputs: inputs(metadata)?,
        artifacts: None,
    };
    let bytes = serde_json::to_vec(&snapshot)?;
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)?
        .write_all(&bytes)?;
    Ok(())
}

pub(super) fn seal(metadata: &Path, coverage: &Path, probe: &Path, path: &Path) -> CheckResult<()> {
    let mut snapshot = read(path)?;
    if snapshot.artifacts.is_some() {
        return Err("Coverage snapshot is already sealed".into());
    }
    verify_inputs(metadata, &snapshot)?;
    snapshot.artifacts = Some(artifacts(coverage, probe)?);
    fs::write(path, serde_json::to_vec(&snapshot)?)?;
    Ok(())
}

pub(super) fn verify(
    metadata: &Path,
    coverage: &Path,
    probe: &Path,
    path: &Path,
) -> CheckResult<()> {
    let snapshot = read(path)?;
    verify_inputs(metadata, &snapshot)?;
    let expected = snapshot
        .artifacts
        .ok_or("Coverage snapshot is not sealed")?;
    if expected != artifacts(coverage, probe)? {
        return Err("Coverage reports changed after collection".into());
    }
    Ok(())
}

fn verify_inputs(path: &Path, snapshot: &Snapshot) -> CheckResult<()> {
    if snapshot.metadata != digest(path, 64 * 1024 * 1024)? {
        return Err("Coverage metadata changed after collection started".into());
    }
    if snapshot.inputs != inputs(path)? {
        return Err("Coverage inputs changed after collection started".into());
    }
    Ok(())
}

fn inputs(path: &Path) -> CheckResult<BTreeMap<PathBuf, Option<String>>> {
    let sources = syntax::inventory(&metadata::source_roots(path)?)?;
    let mut inputs = BTreeMap::new();
    for source in sources
        .keys()
        .chain(metadata::coverage_configuration(path)?.iter())
    {
        let value = match fs::symlink_metadata(source) {
            Ok(info) if info.file_type().is_file() => Some(digest(source, 4 * 1024 * 1024)?),
            Ok(_) => return Err("Coverage inputs must be regular files".into()),
            Err(error) if error.kind() == ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        inputs.insert(source.clone(), value);
    }
    Ok(inputs)
}

fn artifacts(coverage: &Path, probe: &Path) -> CheckResult<[String; 2]> {
    Ok([
        digest(coverage, 256 * 1024 * 1024)?,
        digest(probe, 256 * 1024 * 1024)?,
    ])
}

fn digest(path: &Path, maximum: u64) -> CheckResult<String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(metadata::read_bounded(path, maximum)?)
    ))
}

fn read(path: &Path) -> CheckResult<Snapshot> {
    if !fs::symlink_metadata(path)?.file_type().is_file() {
        return Err("Coverage snapshot must be a regular file".into());
    }
    let snapshot: Snapshot = serde_json::from_slice(&metadata::read_bounded(path, 1024 * 1024)?)?;
    if snapshot.version != 1 || snapshot.inputs.is_empty() {
        return Err("Unknown or empty coverage snapshot".into());
    }
    Ok(snapshot)
}
