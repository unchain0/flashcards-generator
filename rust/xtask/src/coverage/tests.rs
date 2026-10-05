use super::*;
use serde_json::json;

fn report() -> Report {
    Report {
        kind: "llvm.coverage.json.export".into(),
        version: "3.1.0".into(),
        data: vec![Data {
            files: vec![],
            functions: vec![],
        }],
    }
}

fn branch(line: u64) -> Vec<u64> {
    vec![line, 1, line, 9, 1, 1, 0, 0, 4]
}

#[test]
fn probes_require_distinct_locations_and_exact_function_identity() -> CheckResult<()> {
    let mut report = report();
    for (name, minimum) in PROBES {
        report.data[0].functions.push(Function {
            name: format!("coverage_probe::{name}::{{closure}}"),
            branches: (0..minimum)
                .map(|line| branch(u64::try_from(line).unwrap()))
                .collect(),
        });
    }
    assert_eq!(missing_instrumentation(&report)?, Vec::<String>::new());
    report.data[0].functions[1].branches = vec![branch(1), branch(1)];
    assert_eq!(missing_instrumentation(&report)?, vec!["branch_and"]);
    report.data[0].functions[1].name = "coverage_probe::branch_and_fake".into();
    assert_eq!(missing_instrumentation(&report)?, vec!["branch_and"]);
    assert!(!probe_function("other_crate::branch_and", "branch_and"));
    assert!(probe_function(
        "_RNvCsl0i9DhEx8T5_14coverage_probe10branch_and",
        "branch_and"
    ));
    Ok(())
}

#[test]
fn malformed_or_one_sided_probe_records_fail() {
    let mut record = branch(1);
    record[4] = 0;
    assert!(validate_probe_branches(&[&record]).is_err());
    record[4] = 1;
    record[8] = 99;
    assert!(validate_probe_branches(&[&record]).is_err());
    record.pop();
    assert!(validate_probe_branches(&[&record]).is_err());
}

#[test]
fn counters_use_integers_even_when_percentages_round_to_one_hundred() {
    let file = File {
        filename: "production.rs".into(),
        summary: Summary {
            lines: Counter {
                count: 1_000_000,
                covered: 999_999,
            },
            functions: Counter {
                count: 2,
                covered: 2,
            },
            branches: Counter {
                count: 0,
                covered: 0,
            },
        },
    };
    let mut failures = Vec::new();
    check_file(&file, true, &mut failures);
    assert_eq!(failures, vec!["production.rs: lines 999999/1000000"]);
}

#[test]
fn executable_sources_cannot_pass_with_empty_counters() {
    let file = File {
        filename: "production.rs".into(),
        summary: Summary {
            lines: Counter {
                count: 0,
                covered: 0,
            },
            functions: Counter {
                count: 0,
                covered: 0,
            },
            branches: Counter {
                count: 0,
                covered: 0,
            },
        },
    };
    let mut failures = Vec::new();
    check_file(&file, true, &mut failures);
    assert_eq!(
        failures,
        vec!["production.rs: executable source has no line/function counters"]
    );
    failures.clear();
    check_file(&file, false, &mut failures);
    assert_eq!(failures, Vec::<String>::new());
}

#[test]
fn reports_reject_unknown_versions_missing_metrics_and_multiple_datasets() -> CheckResult<()> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("coverage.json");
    let mut value = json!({"type": "llvm.coverage.json.export", "version": "3.1.0",
        "data": [{"files": [], "functions": []}]});
    std::fs::write(&path, serde_json::to_vec(&value)?)?;
    assert!(read_report(&path).is_ok());
    value["version"] = json!("99.0.0");
    std::fs::write(&path, serde_json::to_vec(&value)?)?;
    assert!(read_report(&path).is_err());
    value["version"] = json!("3.1.0");
    value["data"] = json!([{"files": [], "functions": []}, {"files": [], "functions": []}]);
    std::fs::write(&path, serde_json::to_vec(&value)?)?;
    assert!(read_report(&path).is_err());
    value["data"] = json!([{"files": [{"filename": "production.rs", "summary": {
        "lines": {"count": 1, "covered": 1}, "functions": {"count": 1, "covered": 1}
    }}], "functions": []}]);
    std::fs::write(&path, serde_json::to_vec(&value)?)?;
    assert!(read_report(&path).is_err());
    Ok(())
}
