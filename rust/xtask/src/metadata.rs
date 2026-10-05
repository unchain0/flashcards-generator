use crate::CheckResult;
mod evidence_inputs;
use serde::Deserialize;
use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
};

#[derive(Clone, Copy, PartialEq, Eq)]
enum Location {
    Shared,
    Server,
    Local,
    Tool,
}

#[derive(Clone, Copy)]
struct Classification {
    layer: usize,
    location: Location,
}

const PACKAGES: [(&str, &str, usize, Location); 10] = [
    ("flashcards-domain", "domain", 0, Location::Shared),
    ("flashcards-engines", "engines", 1, Location::Shared),
    ("flashcards-services", "services", 2, Location::Shared),
    (
        "flashcards-integrations",
        "integrations",
        3,
        Location::Local,
    ),
    ("flashcards-delivery", "delivery", 4, Location::Local),
    ("xtask", "xtask", 5, Location::Tool),
    (
        "flashcards-integrations-shared",
        "integrations-shared",
        3,
        Location::Shared,
    ),
    (
        "flashcards-integrations-server",
        "integrations-server",
        3,
        Location::Server,
    ),
    (
        "flashcards-delivery-shared",
        "delivery-shared",
        4,
        Location::Shared,
    ),
    (
        "flashcards-delivery-server",
        "delivery-server",
        4,
        Location::Server,
    ),
];

#[derive(Deserialize)]
struct Metadata {
    packages: Vec<Package>,
    workspace_members: Vec<String>,
    workspace_root: PathBuf,
    resolve: Resolve,
    version: u64,
}

#[derive(Deserialize)]
struct Package {
    id: String,
    name: String,
    source: Option<String>,
    manifest_path: PathBuf,
    dependencies: Vec<Dependency>,
}

#[derive(Deserialize)]
struct Dependency {
    name: String,
    path: Option<PathBuf>,
    kind: Option<String>,
}

#[derive(Deserialize)]
struct Resolve {
    nodes: Vec<Node>,
}

#[derive(Deserialize)]
struct Node {
    id: String,
    deps: Vec<ResolvedDependency>,
}

#[derive(Deserialize)]
struct ResolvedDependency {
    pkg: String,
}

pub(super) fn check(path: &Path) -> CheckResult<()> {
    let metadata: Metadata = serde_json::from_slice(&read_bounded(path, 64 * 1024 * 1024)?)?;
    validate(&metadata)?;
    println!(
        "MASA declarations, data-location boundaries, resolved package IDs, source inventory, and lint inheritance passed"
    );
    Ok(())
}

fn validate(metadata: &Metadata) -> CheckResult<()> {
    if metadata.version != 1 || metadata.workspace_members.len() != PACKAGES.len() {
        return Err("Unknown metadata version or unclassified workspace member".into());
    }
    let layers = classify(metadata)?;
    for package in metadata
        .packages
        .iter()
        .filter(|package| package.source.is_none())
    {
        check_manifest(package)?;
        check_declared(package, metadata, &layers)?;
    }
    for node in &metadata.resolve.nodes {
        check_resolved(node, &layers)?;
    }
    Ok(())
}

fn classify(metadata: &Metadata) -> CheckResult<HashMap<String, Classification>> {
    let mut layers = HashMap::new();
    for package in metadata
        .packages
        .iter()
        .filter(|package| package.source.is_none())
    {
        let (_, directory, layer, location) = PACKAGES
            .iter()
            .find(|(name, _, _, _)| *name == package.name)
            .ok_or("Unclassified local dependency")?;
        let expected = metadata
            .workspace_root
            .join("rust")
            .join(directory)
            .join("Cargo.toml");
        if expected.canonicalize()? != package.manifest_path.canonicalize()?
            || !metadata.workspace_members.contains(&package.id)
        {
            return Err(format!("Unclassified local source for {}", package.name).into());
        }
        layers.insert(
            package.id.clone(),
            Classification {
                layer: *layer,
                location: *location,
            },
        );
    }
    if layers.len() != PACKAGES.len() {
        return Err("Missing classified workspace package".into());
    }
    Ok(layers)
}

fn check_manifest(package: &Package) -> CheckResult<()> {
    let bytes = read_bounded(&package.manifest_path, 65_536)?;
    let manifest: toml::Value = toml::from_str(std::str::from_utf8(&bytes)?)?;
    if manifest
        .get("lints")
        .and_then(|lints| lints.get("workspace"))
        .and_then(toml::Value::as_bool)
        != Some(true)
    {
        return Err(format!("{} does not inherit workspace lints", package.name).into());
    }
    Ok(())
}

fn check_declared(
    package: &Package,
    metadata: &Metadata,
    layers: &HashMap<String, Classification>,
) -> CheckResult<()> {
    let layer = *layers
        .get(&package.id)
        .ok_or("Missing package classification")?;
    for dependency in &package.dependencies {
        if !external_location_allowed(layer, dependency) {
            return Err(format!(
                "{} cannot acquire external capability {}",
                package.name, dependency.name
            )
            .into());
        }
        check_dependency(package, dependency, metadata, layers, layer.layer)?;
    }
    Ok(())
}

fn external_location_allowed(source: Classification, dependency: &Dependency) -> bool {
    if source.layer < 3 || dependency.path.is_some() {
        return true;
    }
    match source.location {
        Location::Local | Location::Tool => true,
        Location::Shared => matches!(
            dependency.name.as_str(),
            "reqwest"
                | "serde"
                | "serde_json"
                | "tokio"
                | "tokio-util"
                | "tracing"
                | "tracing-subscriber"
                | "sentry"
                | "axum"
                | "futures-util"
        ),
        Location::Server => matches!(
            dependency.name.as_str(),
            "argon2"
                | "base64"
                | "getrandom"
                | "hmac"
                | "sha2"
                | "serde"
                | "serde_json"
                | "sqlx"
                | "tokio"
                | "tracing"
                | "axum"
                | "tower"
                | "tower-http"
        ),
    }
}

fn check_dependency(
    package: &Package,
    dependency: &Dependency,
    metadata: &Metadata,
    layers: &HashMap<String, Classification>,
    layer: usize,
) -> CheckResult<()> {
    if let Some(path) = &dependency.path {
        let manifest = path.join("Cargo.toml").canonicalize()?;
        let target = metadata
            .packages
            .iter()
            .find(|candidate| candidate.manifest_path == manifest)
            .ok_or("Declared local dependency absent from metadata")?;
        return inward(&package.id, &target.id, layers);
    }
    if external_allowed(layer, dependency) {
        return Ok(());
    }
    Err(format!(
        "{} cannot depend on external capability {}",
        package.name, dependency.name
    )
    .into())
}

fn external_allowed(layer: usize, dependency: &Dependency) -> bool {
    matches!(
        (layer, dependency.kind.as_deref(), dependency.name.as_str()),
        (1, None, "regex" | "lazy-regex")
            | (2, None, "serde")
            | (1 | 2, Some("dev"), "serde" | "serde_json")
            | (3.., _, _)
    )
}

fn check_resolved(node: &Node, layers: &HashMap<String, Classification>) -> CheckResult<()> {
    if !layers.contains_key(&node.id) {
        return Ok(());
    }
    for dependency in &node.deps {
        if layers.contains_key(&dependency.pkg) {
            inward(&node.id, &dependency.pkg, layers)?;
        }
    }
    Ok(())
}

fn inward(source: &str, target: &str, layers: &HashMap<String, Classification>) -> CheckResult<()> {
    let source_layer = layers.get(source).ok_or("Unclassified source package ID")?;
    let target_layer = layers
        .get(target)
        .ok_or("Unclassified dependency package ID")?;
    let locations_allowed = match source_layer.location {
        Location::Server => target_layer.location != Location::Local,
        Location::Shared => target_layer.location == Location::Shared,
        Location::Local | Location::Tool => true,
    };
    let shared_peer = target_layer.layer == source_layer.layer
        && target_layer.location == Location::Shared
        && source_layer.location != Location::Shared;
    if !locations_allowed || (target_layer.layer >= source_layer.layer && !shared_peer) {
        return Err(format!(
            "Outward MASA or forbidden data-location dependency: {source} -> {target}"
        )
        .into());
    }
    Ok(())
}

pub(super) fn read_bounded(path: &Path, maximum: u64) -> CheckResult<Vec<u8>> {
    use std::io::Read;
    let file = fs::File::open(path)?;
    let mut bytes = Vec::new();
    file.take(maximum + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > maximum {
        return Err("Quality input exceeds size limit".into());
    }
    Ok(bytes)
}

pub(super) fn source_roots(path: &Path) -> CheckResult<Vec<PathBuf>> {
    let metadata: Metadata = serde_json::from_slice(&read_bounded(path, 64 * 1024 * 1024)?)?;
    validate(&metadata)?;
    Ok(PACKAGES
        .iter()
        .filter(|(_, _, layer, _)| *layer < 5)
        .map(|(_, directory, _, _)| {
            metadata
                .workspace_root
                .join("rust")
                .join(directory)
                .join("src")
        })
        .collect())
}

#[cfg(test)]
mod tests;

pub(super) fn coverage_configuration(path: &Path) -> CheckResult<Vec<PathBuf>> {
    let metadata: Metadata = serde_json::from_slice(&read_bounded(path, 64 * 1024 * 1024)?)?;
    validate(&metadata)?;
    let mut paths = metadata
        .packages
        .iter()
        .filter(|package| package.source.is_none())
        .map(|package| package.manifest_path.clone())
        .collect::<Vec<_>>();
    paths.extend(
        [
            "Cargo.toml",
            "Cargo.lock",
            "clippy.toml",
            "deny.toml",
            "rust-toolchain.toml",
            ".cargo/config.toml",
            "ci/coverage-toolchain",
            "ci/coverage-probe.rs",
            "scripts/rust-coverage.sh",
            "scripts/rust-coverage-probe.sh",
            "scripts/rust-check.sh",
            "scripts/rust-live-companion-check.sh",
            "frontend/playwright.rust.config.ts",
            "frontend/playwright.live.config.ts",
            "frontend/playwright.config.ts",
            "frontend/package.json",
            "frontend/pnpm-lock.yaml",
            "frontend/vite.config.ts",
            "frontend/tsconfig.json",
            "frontend/index.html",
            "frontend/e2e/live-generation.live.ts",
            "rust/delivery/examples/notebooklm_browser_smoke.rs",
            "rust/delivery/examples/notebooklm_companion_smoke.rs",
            "rust/engines/src/quality_stopwords.txt",
        ]
        .map(|name| metadata.workspace_root.join(name)),
    );
    paths.extend(migration_inputs(&metadata.workspace_root)?);
    for package in metadata
        .packages
        .iter()
        .filter(|package| package.source.is_none())
    {
        let root = package
            .manifest_path
            .parent()
            .ok_or("Missing package directory")?;
        evidence_inputs::collect(root, 0, &mut paths)?;
    }
    for directory in ["frontend/src", "frontend/e2e", "ci", "scripts"] {
        evidence_inputs::collect(&metadata.workspace_root.join(directory), 0, &mut paths)?;
    }
    Ok(paths)
}

fn migration_inputs(root: &Path) -> CheckResult<Vec<PathBuf>> {
    let directory = root.join("rust/integrations-server/migrations");
    let info = match fs::symlink_metadata(&directory) {
        Ok(info) => info,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
    };
    if !info.file_type().is_dir() {
        return Err("Migration source must be a regular directory".into());
    }
    let mut paths = Vec::new();
    for entry in fs::read_dir(directory)? {
        let path = entry?.path();
        if path.extension().is_some_and(|extension| extension == "sql") {
            paths.push(path);
        }
    }
    Ok(paths)
}
