use super::*;
use crate::document_inputs::safe_filenames;

#[derive(Clone, Copy)]
enum InitialState {
    Queued,
    Running,
    Processed,
    SourceFailed,
    Completed,
    Failed,
    Cancelled,
}

fn state_at(initial: InitialState) -> LocalJobState {
    let names = safe_filenames(&["first.pdf".into(), "second.pptx".into()]).unwrap();
    let mut state = LocalJobState::new(&"a".repeat(32), &names).unwrap();
    if matches!(initial, InitialState::Queued) {
        return state;
    }
    state.start().unwrap();
    match initial {
        InitialState::Processed | InitialState::Completed => {
            state.record_source(SourceOutcome::Completed).unwrap();
            state.record_source(SourceOutcome::Skipped).unwrap();
        }
        InitialState::SourceFailed => {
            state.record_source(SourceOutcome::Completed).unwrap();
            state.record_source(SourceOutcome::Failed).unwrap();
        }
        _ => {}
    }
    match initial {
        InitialState::Completed => state.finish(JobStatus::Completed, vec![]).unwrap(),
        InitialState::Failed => state.finish(JobStatus::Failed, vec![]).unwrap(),
        InitialState::Cancelled => state.finish(JobStatus::Cancelled, vec![]).unwrap(),
        _ => {}
    }
    state
}

#[test]
fn every_completion_request_preserves_the_browser_contract_and_rejected_state() {
    use JobStatus::{Cancelled, Completed, Failed, Queued, Running};
    let cases: [(InitialState, &[JobStatus]); 7] = [
        (InitialState::Queued, &[Cancelled]),
        (InitialState::Running, &[Failed, Cancelled]),
        (InitialState::Processed, &[Completed, Failed, Cancelled]),
        (InitialState::SourceFailed, &[Failed, Cancelled]),
        (InitialState::Completed, &[]),
        (InitialState::Failed, &[]),
        (InitialState::Cancelled, &[]),
    ];
    for (stage, allowed, target) in cases.into_iter().flat_map(|(stage, allowed)| {
        [Queued, Running, Completed, Failed, Cancelled]
            .into_iter()
            .map(move |target| (stage, allowed, target))
    }) {
        let mut state = state_at(stage);
        let before = serde_json::to_value(state.snapshot()).unwrap();
        let valid = allowed.contains(&target);
        assert_eq!(state.validate_finish(target).is_ok(), valid);
        assert_eq!(serde_json::to_value(state.snapshot()).unwrap(), before);
        let result = state.finish(target, vec!["b.csv".into(), "a.csv".into(), "a.csv".into()]);
        assert_eq!(result.is_ok(), valid);
        if !valid {
            assert_eq!(serde_json::to_value(state.snapshot()).unwrap(), before);
            continue;
        }
        let mut expected = before;
        expected["status"] = serde_json::to_value(target).unwrap();
        expected["artifacts"] = serde_json::json!(["a.csv", "b.csv"]);
        expected["message"] = serde_json::json!(match target {
            Completed => "Geração concluída.",
            Failed => "A geração falhou. Confira a conexão do NotebookLM e tente novamente.",
            Cancelled => "Geração cancelada.",
            _ => panic!("Only declared terminal cases may succeed"),
        });
        expected["error"] = if target == Failed {
            serde_json::json!("Falha na geração local.")
        } else {
            serde_json::Value::Null
        };
        assert_eq!(serde_json::to_value(state.snapshot()).unwrap(), expected);
    }
}

#[test]
fn excessive_source_counts_and_transition_diagnostics_do_not_expose_private_data() {
    let filename =
        crate::document_inputs::SourceFilename::from_upload(1, "private-source.pdf").unwrap();
    let error = LocalJobState::new(&"a".repeat(32), &vec![filename; MAX_JOB_FILES + 1])
        .err()
        .unwrap();
    assert_eq!(error.to_string(), "Invalid local job state transition");
    assert!(error.source().is_none());
}

#[test]
fn preserves_browser_counts_and_rejects_completion_after_cancellation() {
    let filenames = safe_filenames(&["a.pdf".into(), "b.pptx".into()]).unwrap();
    let id = "a".repeat(32);
    let mut state = LocalJobState::new(&id, &filenames).unwrap();
    assert_eq!(state.status(), JobStatus::Queued);
    assert_eq!(state.snapshot().discovered_sources, 0);
    assert!(state.record_source(SourceOutcome::Completed).is_err());
    assert!(state.finish(JobStatus::Completed, Vec::new()).is_err());
    state.start().unwrap();
    assert!(state.start().is_err());
    assert!(state.finish(JobStatus::Completed, Vec::new()).is_err());
    state.record_source(SourceOutcome::Completed).unwrap();
    state.record_source(SourceOutcome::Skipped).unwrap();
    assert!(state.record_source(SourceOutcome::Completed).is_err());
    state
        .finish(JobStatus::Completed, vec!["a.csv".into(), "a.csv".into()])
        .unwrap();
    let snapshot = state.snapshot();
    assert_eq!(snapshot.artifacts, vec!["a.csv"]);
    assert_eq!(snapshot.completed_sources, 1);
    assert_eq!(snapshot.skipped_sources, 1);
    assert!(snapshot.error.is_none());
    assert_eq!(
        serde_json::to_value(&snapshot).unwrap()["status"],
        "completed"
    );
    assert!(state.finish(JobStatus::Failed, Vec::new()).is_err());
    let mut cancelled = LocalJobState::new(&id, &filenames).unwrap();
    cancelled.finish(JobStatus::Cancelled, Vec::new()).unwrap();
    assert!(cancelled.start().is_err());
    assert!(cancelled.finish(JobStatus::Completed, Vec::new()).is_err());
    let mut failed = LocalJobState::new(&id, &filenames).unwrap();
    failed.start().unwrap();
    failed.record_source(SourceOutcome::Failed).unwrap();
    assert!(failed.finish(JobStatus::Completed, Vec::new()).is_err());
    assert!(failed.finish(JobStatus::Running, Vec::new()).is_err());
    failed
        .finish(JobStatus::Failed, vec!["partial.csv".into()])
        .unwrap();
    assert_eq!(failed.snapshot().artifacts, vec!["partial.csv"]);
    assert_eq!(failed.snapshot().failed_sources, 1);
    assert_eq!(
        failed.snapshot().error.as_deref(),
        Some("Falha na geração local.")
    );
    assert!(LocalJobState::new("../", &filenames).is_err());
    assert!(LocalJobState::new(&id, &[]).is_err());
}
