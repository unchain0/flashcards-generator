use super::*;
use crate::document_inputs::safe_filenames;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

struct Resource(Arc<AtomicUsize>);
impl Drop for Resource {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}

#[test]
fn registry_diagnostics_remain_public_and_have_no_private_error_causes() {
    for error in [
        RegistryError::Closed,
        RegistryError::Full,
        RegistryError::Missing,
        RegistryError::InvalidState,
    ] {
        let message = error.to_string();
        assert_ne!(message, "");
        assert!(!message.contains("private"));
        assert!(error.source().is_none());
    }
}

#[test]
fn cancelled_reservations_reject_submission_and_expiry_preserves_pending_work() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let now = Instant::now();
    let drops = Arc::new(AtomicUsize::new(0));
    let mut jobs = LocalJobRegistry::default();
    assert_eq!(jobs.next_expiration(), None);
    for (id, delay) in [("b".repeat(32), 10), ("c".repeat(32), 0)] {
        jobs.reserve(owner.clone(), &id, &names, Resource(drops.clone()), now)
            .unwrap();
        let finished = jobs
            .finish(
                &owner,
                &id,
                JobStatus::Cancelled,
                vec![],
                now + Duration::from_secs(delay),
            )
            .unwrap();
        assert_eq!(
            jobs.submit(&owner, &id).unwrap_err(),
            RegistryError::InvalidState
        );
        assert_eq!(
            jobs.discard_reservation(&owner, &id),
            Err(RegistryError::InvalidState)
        );
        assert_eq!(
            serde_json::to_value(jobs.snapshot(&owner, &id, now).unwrap()).unwrap(),
            serde_json::to_value(finished).unwrap()
        );
    }
    let pending = "d".repeat(32);
    jobs.reserve(
        owner.clone(),
        &pending,
        &names,
        Resource(drops.clone()),
        now,
    )
    .unwrap();
    jobs.submit(&owner, &pending).unwrap();
    assert_eq!(jobs.next_expiration(), Some(now + JOB_RETENTION));
    jobs.prune(now.checked_sub(Duration::from_secs(1)).unwrap());
    assert_eq!(drops.load(Ordering::SeqCst), 0);
    jobs.prune(now + JOB_RETENTION);
    assert_eq!(drops.load(Ordering::SeqCst), 1);
    assert_eq!(
        jobs.next_expiration(),
        Some(now + JOB_RETENTION + Duration::from_secs(10))
    );
    assert!(jobs.snapshot(&owner, &"c".repeat(32), now).is_none());
    assert!(jobs.resource(&owner, &pending, now).is_some());
    jobs.prune(now + JOB_RETENTION + Duration::from_secs(10));
    assert_eq!(drops.load(Ordering::SeqCst), 2);
    assert_eq!(jobs.next_expiration(), None);
    assert_eq!(jobs.start_next().unwrap().unwrap().1.id, pending);
    jobs.close();
    assert!(jobs.is_closed());
    assert_eq!(drops.load(Ordering::SeqCst), 3);
}

#[test]
fn rejected_source_results_preserve_the_registered_job_snapshot() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let now = Instant::now();
    let id = "b".repeat(32);
    let mut jobs = LocalJobRegistry::default();
    jobs.reserve(owner.clone(), &id, &names, (), now).unwrap();
    for stage in 0..3 {
        let before = serde_json::to_value(jobs.snapshot(&owner, &id, now).unwrap()).unwrap();
        assert_eq!(
            jobs.record_source(&owner, &id, SourceOutcome::Failed),
            Err(RegistryError::InvalidState)
        );
        assert_eq!(
            serde_json::to_value(jobs.snapshot(&owner, &id, now).unwrap()).unwrap(),
            before
        );
        if stage == 0 {
            jobs.submit(&owner, &id).unwrap();
            jobs.start_next().unwrap().unwrap();
            jobs.record_source(&owner, &id, SourceOutcome::Completed)
                .unwrap();
        } else if stage == 1 {
            jobs.finish(
                &owner,
                &id,
                JobStatus::Completed,
                vec!["deck.csv".into()],
                now,
            )
            .unwrap();
        }
    }
    assert_eq!(
        jobs.snapshot(&owner, &id, now).unwrap().artifacts,
        ["deck.csv"]
    );
}

#[test]
fn bounds_active_work_runs_fifo_and_guards_every_user_operation() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let stranger = UserId::try_from("b".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let now = Instant::now();
    let drops = Arc::new(AtomicUsize::new(0));
    let mut jobs = LocalJobRegistry::default();
    for id in 1..=3 {
        jobs.reserve(
            owner.clone(),
            &format!("{id:032x}"),
            &names,
            Resource(drops.clone()),
            now,
        )
        .unwrap();
    }
    assert_eq!(
        jobs.reserve(
            owner.clone(),
            &format!("{:032x}", 4),
            &names,
            Resource(drops.clone()),
            now
        )
        .unwrap_err(),
        RegistryError::Full
    );
    assert_eq!(drops.load(Ordering::SeqCst), 1);
    let first = format!("{:032x}", 1);
    let second = format!("{:032x}", 2);
    let third = format!("{:032x}", 3);
    assert!(jobs.start_next().unwrap().is_none());
    assert_foreign_job_access_rejected(&mut jobs, &stranger, &first, now);
    jobs.submit(&owner, &second).unwrap();
    jobs.submit(&owner, &first).unwrap();
    assert_eq!(
        jobs.submit(&owner, &first).unwrap_err(),
        RegistryError::InvalidState
    );
    assert_eq!(
        jobs.discard_reservation(&owner, &first).unwrap_err(),
        RegistryError::InvalidState
    );
    assert_eq!(jobs.start_next().unwrap().unwrap().1.id, second);
    assert!(jobs.start_next().unwrap().is_none());
    assert!(
        jobs.finish(&owner, &second, JobStatus::Completed, vec![], now)
            .is_err()
    );
    jobs.record_source(&owner, &second, SourceOutcome::Completed)
        .unwrap();
    jobs.finish(
        &owner,
        &second,
        JobStatus::Completed,
        vec!["deck.csv".into()],
        now,
    )
    .unwrap();
    assert!(jobs.resource(&owner, &second, now).is_some());
    assert_eq!(jobs.start_next().unwrap().unwrap().1.id, first);
    jobs.finish(&owner, &first, JobStatus::Cancelled, vec![], now)
        .unwrap();
    assert!(
        jobs.finish(&owner, &first, JobStatus::Failed, vec![], now)
            .is_err()
    );
    jobs.discard_reservation(&owner, &third).unwrap();
    assert_eq!(drops.load(Ordering::SeqCst), 2);
    jobs.prune(
        (now + JOB_RETENTION)
            .checked_sub(Duration::from_nanos(1))
            .unwrap(),
    );
    assert!(jobs.snapshot(&owner, &first, now).is_some());
    jobs.prune(now + JOB_RETENTION);
    assert!(jobs.snapshot(&owner, &first, now).is_none());
    assert_eq!(drops.load(Ordering::SeqCst), 4);
}

fn assert_foreign_job_access_rejected(
    jobs: &mut LocalJobRegistry<Resource>,
    stranger: &UserId,
    id: &str,
    now: Instant,
) {
    assert!(jobs.snapshot(stranger, id, now).is_none());
    assert!(jobs.resource(stranger, id, now).is_none());
    assert_eq!(
        jobs.submit(stranger, id).unwrap_err(),
        RegistryError::Missing
    );
    assert_eq!(
        jobs.discard_reservation(stranger, id).unwrap_err(),
        RegistryError::Missing
    );
    assert_eq!(
        jobs.record_source(stranger, id, SourceOutcome::Completed),
        Err(RegistryError::Missing)
    );
    assert_eq!(
        jobs.validate_finish(stranger, id, JobStatus::Cancelled),
        Err(RegistryError::Missing)
    );
    assert_eq!(
        jobs.finish(stranger, id, JobStatus::Cancelled, vec![], now)
            .unwrap_err(),
        RegistryError::Missing
    );
}

#[test]
fn invalid_queue_entries_report_errors_without_losing_pending_work() {
    let mut jobs = LocalJobRegistry::default();
    let id = "a".repeat(32);
    jobs.queue.push_back(id.clone());
    assert_eq!(jobs.start_next().unwrap_err(), RegistryError::Missing);
    assert_eq!(jobs.queue.front(), Some(&id));
    let owner = UserId::try_from("b".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    jobs.reserve(owner, &id, &names, (), Instant::now())
        .unwrap();
    jobs.jobs
        .get_mut(&id)
        .unwrap()
        .state
        .finish(JobStatus::Cancelled, vec![])
        .unwrap();
    assert_eq!(jobs.start_next().unwrap_err(), RegistryError::InvalidState);
    assert_eq!(jobs.queue.front(), Some(&id));
}

#[test]
fn evicts_only_oldest_finished_resources_and_rejects_closed_or_duplicate_reservations() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let now = Instant::now();
    let drops = Arc::new(AtomicUsize::new(0));
    let mut jobs = LocalJobRegistry::default();
    let active = format!("{:032x}", 100);
    jobs.reserve(owner.clone(), &active, &names, Resource(drops.clone()), now)
        .unwrap();
    for id in 1..=20 {
        let id = format!("{id:032x}");
        jobs.reserve(owner.clone(), &id, &names, Resource(drops.clone()), now)
            .unwrap();
        jobs.finish(
            &owner,
            &id,
            JobStatus::Cancelled,
            vec![],
            now + Duration::from_secs(drops.load(Ordering::SeqCst) as u64),
        )
        .unwrap();
    }
    assert!(jobs.snapshot(&owner, &active, now).is_some());
    assert_eq!(jobs.jobs.len(), MAX_RETAINED_JOBS);
    assert_eq!(drops.load(Ordering::SeqCst), 1);
    assert!(jobs.snapshot(&owner, &format!("{:032x}", 1), now).is_none());
    assert_eq!(
        jobs.reserve(owner.clone(), &active, &names, Resource(drops.clone()), now)
            .unwrap_err(),
        RegistryError::InvalidState
    );
    assert_eq!(
        jobs.reserve(owner.clone(), "../", &names, Resource(drops.clone()), now)
            .unwrap_err(),
        RegistryError::InvalidState
    );
    assert_eq!(
        jobs.reserve(
            owner.clone(),
            &format!("{:032x}", 101),
            &[],
            Resource(drops.clone()),
            now
        )
        .unwrap_err(),
        RegistryError::InvalidState
    );
    jobs.prune(now + JOB_RETENTION + Duration::from_secs(2));
    assert_eq!(jobs.jobs.len(), 1);
    assert!(jobs.resource(&owner, &active, now).is_some());
    jobs.close();
    jobs.close();
    assert!(jobs.snapshot(&owner, &active, now).is_none());
    assert!(jobs.start_next().unwrap().is_none());
    assert_eq!(
        jobs.submit(&owner, &active).unwrap_err(),
        RegistryError::Closed
    );
    assert_eq!(
        jobs.reserve(owner, &active, &names, Resource(drops.clone()), now)
            .unwrap_err(),
        RegistryError::Closed
    );
    assert_eq!(drops.load(Ordering::SeqCst), 25);
}
