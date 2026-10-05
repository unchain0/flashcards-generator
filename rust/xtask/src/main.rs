mod coverage;
mod coverage_evidence;
mod metadata;
mod syntax;

use std::{error::Error, path::Path, process::ExitCode};

type CheckResult<T> = Result<T, Box<dyn Error>>;

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> CheckResult<()> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    match arguments.as_slice() {
        [command, input] if command == "check-masa" => metadata::check(Path::new(input)),
        [command, metadata, snapshot] if command == "snapshot-coverage" =>
            coverage_evidence::capture(Path::new(metadata), Path::new(snapshot)),
        [command, metadata, report, probe, snapshot] if command == "seal-coverage" =>
            coverage_evidence::seal(Path::new(metadata), Path::new(report), Path::new(probe), Path::new(snapshot)),
        [command, metadata, report, probe, snapshot] if command == "check-coverage" =>
            coverage::check_collected(Path::new(metadata), Path::new(report), Path::new(probe), Path::new(snapshot)),
        [command, probe] if command == "check-instrumentation" =>
            coverage::check_instrumentation(Path::new(probe)),
        _ => Err("Use xtask check-masa <metadata>, snapshot-coverage <metadata> <snapshot>, seal-coverage <metadata> <coverage> <probe> <snapshot>, check-coverage <metadata> <coverage> <probe> <snapshot>, or check-instrumentation <probe>".into()),
    }
}
