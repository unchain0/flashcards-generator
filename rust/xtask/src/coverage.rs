use crate::{CheckResult, coverage_evidence, metadata, syntax};
use serde::Deserialize;
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

#[derive(Deserialize)]
struct Report {
    #[serde(rename = "type")]
    kind: String,
    version: String,
    data: Vec<Data>,
}

#[derive(Deserialize)]
struct Data {
    files: Vec<File>,
    functions: Vec<Function>,
}

#[derive(Deserialize)]
struct File {
    filename: String,
    summary: Summary,
}

#[derive(Deserialize)]
struct Summary {
    lines: Counter,
    functions: Counter,
    branches: Counter,
}

#[derive(Deserialize)]
struct Counter {
    count: u64,
    covered: u64,
}

#[derive(Deserialize)]
struct Function {
    name: String,
    branches: Vec<Vec<u64>>,
}

const PROBES: [(&str, usize); 12] = [
    ("branch_if", 1),
    ("branch_and", 2),
    ("branch_or", 2),
    ("branch_match", 1),
    ("branch_question", 1),
    ("branch_let_else", 1),
    ("branch_macro", 1),
    ("branch_async", 2),
    ("branch_if_let", 1),
    ("branch_for", 1),
    ("branch_while", 1),
    ("branch_while_let", 1),
];

pub(super) fn check_instrumentation(path: &Path) -> CheckResult<()> {
    let report = read_report(path)?;
    let unsupported = missing_instrumentation(&report)?;
    if !unsupported.is_empty() {
        return Err(format!(
            "Branch instrumentation not demonstrated: {}",
            unsupported.join(", ")
        )
        .into());
    }
    println!("Branch instrumentation probes passed");
    Ok(())
}

#[derive(Deserialize, Serialize)]
struct Baseline {
    files: BTreeMap<String, BaselineFile>,
}

#[derive(Deserialize, Serialize)]
struct BaselineFile {
    lines: u64,
    branches: u64,
    functions: u64,
}

fn baseline_key(path: &str) -> String {
    path.find("/rust/").map_or_else(
        || path.trim_start_matches("./").to_owned(),
        |index| path[index + 1..].to_owned(),
    )
}

pub(super) fn write_baseline(report: &Path, out: &Path) -> CheckResult<()> {
    let report = read_report(report)?;
    let mut files = BTreeMap::new();
    for file in &report.data[0].files {
        files.insert(
            baseline_key(&file.filename),
            BaselineFile {
                lines: file.summary.lines.covered,
                branches: file.summary.branches.covered,
                functions: file.summary.functions.covered,
            },
        );
    }
    let baseline = Baseline { files };
    std::fs::write(out, serde_json::to_vec_pretty(&baseline)?)?;
    println!("Coverage baseline written to {}", out.display());
    Ok(())
}

fn baseline_entry<'a>(baseline: &'a Baseline, filename: &str) -> Option<&'a BaselineFile> {
    baseline.files.get(filename)
}

pub(super) fn check(metadata: &Path, coverage: &Path, probe: &Path) -> CheckResult<()> {
    check_with_baseline(metadata, coverage, probe, None)
}

pub(super) fn check_collected_ratchet(
    metadata: &Path,
    coverage: &Path,
    probe: &Path,
    snapshot: &Path,
    baseline_path: &Path,
) -> CheckResult<()> {
    coverage_evidence::verify(metadata, coverage, probe, snapshot)?;
    check_ratchet(metadata, coverage, probe, baseline_path)
}

pub(super) fn check_ratchet(
    metadata: &Path,
    coverage: &Path,
    probe: &Path,
    baseline_path: &Path,
) -> CheckResult<()> {
    let baseline = serde_json::from_slice::<Baseline>(&std::fs::read(baseline_path)?)
        .map_err(|error| format!("invalid coverage baseline: {error}"))?;
    check_with_baseline(metadata, coverage, probe, Some(&baseline))
}

fn check_with_baseline(
    metadata: &Path,
    coverage: &Path,
    probe: &Path,
    baseline: Option<&Baseline>,
) -> CheckResult<()> {
    let sources = syntax::inventory(&metadata::source_roots(metadata)?)?;
    let report = read_report(coverage)?;
    let mut missing = sources
        .iter()
        .filter(|(_, source)| source.executable)
        .map(|(path, _)| path.clone())
        .collect::<BTreeSet<_>>();
    let mut failures = Vec::new();
    let mut branches = 0_u64;
    let mut reported = BTreeSet::new();
    for file in &report.data[0].files {
        let path = Path::new(&file.filename).canonicalize()?;
        let Some(source) = sources.get(&path) else {
            continue;
        };
        if !reported.insert(path.clone()) {
            failures.push(format!(
                "Duplicate production coverage file: {}",
                path.display()
            ));
        }
        missing.remove(&path);
        branches = branches
            .checked_add(file.summary.branches.count)
            .ok_or("Invalid overflowing production branch count")?;
        check_file(
            file,
            source.executable,
            baseline.and_then(|value| baseline_entry(value, &baseline_key(&file.filename))),
            baseline.is_some(),
            &mut failures,
        );
    }
    if !missing.is_empty() {
        failures.push(format!(
            "{} executable source files absent from coverage",
            missing.len()
        ));
        failures.extend(
            missing
                .iter()
                .map(|path| format!("Executable source absent from coverage: {}", path.display())),
        );
    }
    if branches == 0 {
        failures.push("No production branch counters were collected".into());
    }
    let probe = read_report(probe)?;
    let missing_probes = missing_instrumentation(&probe)?;
    let used = sources
        .values()
        .flat_map(|source| &source.constructions)
        .collect::<BTreeSet<_>>();
    for name in missing_probes
        .iter()
        .filter(|name| used.contains(&name.as_str()))
    {
        failures.push(format!(
            "Production branch instrumentation not demonstrated: {name}"
        ));
    }
    if !failures.is_empty() {
        return Err(failures.join("\n").into());
    }
    println!("Production source inventory and integer line/function/branch counters passed");
    Ok(())
}

pub(super) fn check_collected(
    metadata: &Path,
    coverage: &Path,
    probe: &Path,
    snapshot: &Path,
) -> CheckResult<()> {
    coverage_evidence::verify(metadata, coverage, probe, snapshot)?;
    check(metadata, coverage, probe)
}

fn counter_failure(
    filename: &str,
    name: &str,
    counter: &Counter,
    floor_value: u64,
    floor: Option<&BaselineFile>,
    ratchet: bool,
) -> Option<String> {
    if ratchet && floor.is_some() {
        return (counter.covered < floor_value).then(|| {
            format!(
                "{filename}: {name} regressed to {}/{} (baseline {floor_value})",
                counter.covered, counter.count
            )
        });
    }
    if counter.covered == counter.count {
        return None;
    }
    if ratchet {
        return Some(format!(
            "{filename}: new file must be fully covered for {name} ({}/{})",
            counter.covered, counter.count
        ));
    }
    Some(format!(
        "{filename}: {name} {}/{}",
        counter.covered, counter.count
    ))
}

fn check_file(
    file: &File,
    executable: bool,
    floor: Option<&BaselineFile>,
    ratchet: bool,
    failures: &mut Vec<String>,
) {
    if executable && (file.summary.lines.count == 0 || file.summary.functions.count == 0) {
        failures.push(format!(
            "{}: executable source has no line/function counters",
            file.filename
        ));
    }
    let minimum = floor.map_or((0, 0, 0), |entry| {
        (entry.lines, entry.branches, entry.functions)
    });
    for (name, counter, floor_value) in [
        ("lines", &file.summary.lines, minimum.0),
        ("functions", &file.summary.functions, minimum.2),
        ("branches", &file.summary.branches, minimum.1),
    ] {
        if let Some(failure) =
            counter_failure(&file.filename, name, counter, floor_value, floor, ratchet)
        {
            failures.push(failure);
        }
    }
}

fn missing_instrumentation(report: &Report) -> CheckResult<Vec<String>> {
    let mut missing = Vec::new();
    for (name, minimum) in PROBES {
        let functions = report.data[0]
            .functions
            .iter()
            .filter(|function| probe_function(&function.name, name));
        let branches = functions
            .flat_map(|function| &function.branches)
            .collect::<Vec<_>>();
        validate_probe_branches(&branches)?;
        let locations = branches
            .iter()
            .map(|branch| &branch[..4])
            .collect::<BTreeSet<_>>();
        if locations.len() < minimum {
            missing.push(name.to_owned());
        }
    }
    Ok(missing)
}

fn probe_function(symbol: &str, name: &str) -> bool {
    let symbol = format!("{:#}", rustc_demangle::demangle(symbol));
    let mut segments = symbol.split("::");
    segments.next() == Some("coverage_probe") && segments.next() == Some(name)
}

fn validate_probe_branches(branches: &[&Vec<u64>]) -> CheckResult<()> {
    for branch in branches {
        if branch.len() != 9 || branch[8] != 4 {
            return Err("Unknown LLVM probe branch record format".into());
        }
        if branch[4] == 0 || branch[5] == 0 {
            return Err("Probe did not exercise both recorded branch outcomes".into());
        }
    }
    Ok(())
}

fn read_report(path: &Path) -> CheckResult<Report> {
    let report: Report = serde_json::from_slice(&metadata::read_bounded(path, 256 * 1024 * 1024)?)?;
    if report.kind != "llvm.coverage.json.export"
        || report.version != "3.1.0"
        || report.data.len() != 1
    {
        return Err("Unknown LLVM coverage format or incompatible merged datasets".into());
    }
    Ok(report)
}

#[cfg(test)]
mod inventory_tests;
#[cfg(test)]
mod tests;
