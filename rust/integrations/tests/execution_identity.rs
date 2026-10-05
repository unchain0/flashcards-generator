use flashcards_domain::identity::UserId;
use flashcards_integrations::local_job_store::LocalJobStore;
use flashcards_services::{
    document_inputs::safe_filenames, generation_options::GenerationOptions, local_jobs::JobStatus,
};
use std::io::Write;

#[tokio::test]
async fn edited_metadata_copies_cannot_redirect_cancellation_or_change_the_next_job() {
    let jobs = LocalJobStore::new().unwrap();
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    let mut submitted = Vec::new();
    for bytes in [b"preserve first source", b"preserve other source"] {
        let reservation = jobs
            .reserve(owner.clone(), names.clone(), GenerationOptions::default())
            .unwrap();
        reservation
            .create_input(&names[0])
            .unwrap()
            .write_all(bytes)
            .unwrap();
        submitted.push(reservation.submit().unwrap());
    }
    let execution = jobs.start_next().unwrap().unwrap();
    assert_eq!(execution.owner(), &owner);
    assert_eq!(execution.snapshot().id, submitted[0].id);
    let input = execution.workspace().unwrap().input_dir().to_owned();
    let mut copied_snapshot = execution.snapshot().clone();
    copied_snapshot.id.clone_from(&submitted[1].id);
    copied_snapshot.status = JobStatus::Completed;
    let copied_owner = UserId::try_from("b".repeat(32)).unwrap();
    assert_ne!(execution.owner(), &copied_owner);
    assert_ne!(execution.snapshot().id, copied_snapshot.id);
    assert_ne!(execution.snapshot().status, copied_snapshot.status);
    drop(execution);
    assert!(!input.exists());
    assert_eq!(
        jobs.snapshot(&owner, &submitted[0].id)
            .unwrap()
            .unwrap()
            .status,
        JobStatus::Cancelled
    );
    assert_eq!(
        jobs.snapshot(&owner, &submitted[1].id)
            .unwrap()
            .unwrap()
            .status,
        JobStatus::Queued
    );
    let next = jobs.start_next().unwrap().unwrap();
    assert_eq!(next.owner(), &owner);
    assert_eq!(next.snapshot().id, submitted[1].id);
    let next_input = next
        .workspace()
        .unwrap()
        .input_dir()
        .join(names[0].as_str());
    assert_eq!(std::fs::read(next_input).unwrap(), b"preserve other source");
    drop(next);
    jobs.close().unwrap();
}
