use super::*;
use flashcards_services::document_inputs::safe_filenames;
use std::io::{Read, Write};

#[test]
fn accepts_the_file_limit_and_rejects_excessive_unique_inputs() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = (0..=MAX_JOB_FILES)
        .map(|index| SourceFilename::from_upload(1, &format!("source-{index}.pdf")).unwrap())
        .collect::<Vec<_>>();
    assert!(matches!(
        LocalJobWorkspace::new(owner.clone(), names.clone()),
        Err(WorkspaceError::InvalidInput)
    ));
    let job = LocalJobWorkspace::new(owner, names[..MAX_JOB_FILES].to_vec()).unwrap();
    assert_eq!(job.filenames().len(), MAX_JOB_FILES);
    let directory = job.input_dir().parent().unwrap().to_owned();
    job.close().unwrap();
    assert!(!directory.exists());
}

#[test]
fn workspace_diagnostics_preserve_causes_without_disclosing_job_paths() {
    let errors = [
        WorkspaceError::InvalidInput,
        WorkspaceError::InvalidArtifact,
        WorkspaceError::from(io::Error::other("synthetic-private-job-path")),
        WorkspaceError::Entropy(getrandom::Error::UNSUPPORTED),
    ];
    for (index, error) in errors.iter().enumerate() {
        assert_eq!(error.source().is_some(), index >= 2);
        assert!(!error.to_string().contains("synthetic-private-job-path"));
    }
    assert_eq!(
        errors[2].source().unwrap().to_string(),
        "synthetic-private-job-path"
    );
}

#[test]
fn bounds_export_names_and_requires_a_regular_file_for_registration() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["study.pdf".into()]).unwrap();
    let mut job = LocalJobWorkspace::new(owner.clone(), names).unwrap();
    let name = format!(
        "{}/{}/{}/{}.CSV",
        "a".repeat(128),
        "b".repeat(128),
        "c".repeat(128),
        "d".repeat(121)
    );
    assert_eq!(name.len(), 512);
    let path = job.output_dir().join(&name);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    assert_eq!(job.export_path(&name).unwrap(), path);
    assert!(matches!(
        job.register_artifact(&name),
        Err(WorkspaceError::Io(_))
    ));
    assert!(job.open_artifact(&owner, &name).unwrap().is_none());
    fs::write(&path, b"bounded CSV").unwrap();
    assert_eq!(job.export_path(&name).unwrap(), path);
    job.register_artifact(&name).unwrap();
    assert!(job.open_artifact(&owner, &name).unwrap().is_some());
    for invalid in [format!("x{name}"), "invalid\0.csv".into()] {
        assert!(matches!(
            job.export_path(&invalid),
            Err(WorkspaceError::InvalidArtifact)
        ));
        assert!(matches!(
            job.register_artifact(&invalid),
            Err(WorkspaceError::InvalidArtifact)
        ));
    }
    #[cfg(target_os = "linux")]
    {
        let excessive_component = format!("{}.csv", "x".repeat(256));
        assert!(matches!(
            job.export_path(&excessive_component),
            Err(WorkspaceError::Io(_))
        ));
    }
    assert_eq!(job.artifact_names().collect::<Vec<_>>(), [name.as_str()]);
    assert_eq!(fs::read(path).unwrap(), b"bounded CSV");
}

#[cfg(unix)]
#[test]
fn rejects_a_replaced_workspace_root_before_removing_external_inputs() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let job = LocalJobWorkspace::new(owner, names).unwrap();
    let root = job.input_dir().parent().unwrap().to_owned();
    let holder = tempfile::tempdir().unwrap();
    let saved = holder.path().join("saved-workspace");
    let external = tempfile::tempdir().unwrap();
    fs::create_dir(external.path().join("input")).unwrap();
    let original = external.path().join("input/private.pdf");
    fs::write(&original, b"preserve external source").unwrap();
    fs::rename(&root, &saved).unwrap();
    std::os::unix::fs::symlink(external.path(), &root).unwrap();
    let result = job.remove_inputs();
    fs::remove_file(&root).unwrap();
    fs::rename(saved, root).unwrap();
    assert!(result.is_err());
    assert_eq!(fs::read(original).unwrap(), b"preserve external source");
    assert!(job.input_dir().exists());
}

#[test]
fn isolates_job_files_and_artifacts_and_removes_only_owned_temporary_data() {
    let owner = UserId::try_from("a".repeat(32)).unwrap();
    let other = UserId::try_from("b".repeat(32)).unwrap();
    let names = safe_filenames(&["source.pdf".into()]).unwrap();
    let mut job = LocalJobWorkspace::new(owner.clone(), names.clone()).unwrap();
    assert_eq!(job.id().len(), 32);
    assert!(UserId::try_from(job.id().to_owned()).is_ok());
    assert_eq!(job.owner(), &owner);
    assert_eq!(job.filenames(), names);
    let second = LocalJobWorkspace::new(owner.clone(), names.clone()).unwrap();
    assert_ne!(job.id(), second.id());
    assert_ne!(job.input_dir(), second.input_dir());
    let root = job.input_dir().parent().unwrap().to_owned();
    let mut input = job.create_input(&names[0]).unwrap();
    input.write_all(b"local source").unwrap();
    drop(input);
    assert!(job.create_input(&names[0]).is_err());
    let alien = safe_filenames(&["alien.pptx".into()]).unwrap();
    assert!(job.create_input(&alien[0]).is_err());
    #[cfg(unix)]
    assert_workspace_permissions(&job, &root, &names[0]);
    let path = job.output_dir().join("deck.csv");
    fs::write(&path, b"cards").unwrap();
    job.register_artifact("deck.csv").unwrap();
    assert_eq!(job.artifact_names().collect::<Vec<_>>(), vec!["deck.csv"]);
    assert!(job.open_artifact(&other, "deck.csv").unwrap().is_none());
    assert!(
        job.open_artifact(&owner, "../outside.csv")
            .unwrap()
            .is_none()
    );
    let mut download = job.open_artifact(&owner, "deck.csv").unwrap().unwrap();
    let mut content = String::new();
    download.read_to_string(&mut content).unwrap();
    assert_eq!(content, "cards");
    drop(download);
    assert_artifact_bounds(&mut job, &path, &owner);
    fs::create_dir(job.output_dir().join("nested")).unwrap();
    fs::write(job.output_dir().join("nested/cards.csv"), b"nested").unwrap();
    job.register_artifact("nested/cards.csv").unwrap();
    let external = tempfile::tempdir().unwrap();
    let external_file = external.path().join("private.csv");
    fs::write(&external_file, b"preserve").unwrap();
    #[cfg(unix)]
    assert_external_paths(
        &mut job,
        &root,
        external.path(),
        &external_file,
        &names,
        &owner,
        &path,
    );
    job.remove_inputs().unwrap();
    job.remove_inputs().unwrap();
    assert!(!job.input_dir().exists());
    assert!(
        job.open_artifact(&owner, "nested/cards.csv")
            .unwrap()
            .is_some()
    );
    job.close().unwrap();
    assert!(!root.exists());
    assert_eq!(fs::read(&external_file).unwrap(), b"preserve");
    assert!(second.input_dir().exists());
    let second_root = second.input_dir().parent().unwrap().to_owned();
    drop(second);
    assert!(!second_root.exists());
    assert!(LocalJobWorkspace::new(owner.clone(), Vec::new()).is_err());
    assert!(LocalJobWorkspace::new(owner, vec![names[0].clone(); 2]).is_err());
}

fn assert_artifact_bounds(job: &mut LocalJobWorkspace, path: &Path, owner: &UserId) {
    OpenOptions::new()
        .write(true)
        .open(path)
        .unwrap()
        .set_len(MAX_DOCUMENT_BYTES + 1)
        .unwrap();
    assert!(job.register_artifact("deck.csv").is_err());
    assert!(job.open_artifact(owner, "deck.csv").is_err());
    fs::write(path, b"cards").unwrap();
    for name in [
        "",
        "../outside.csv",
        "/tmp/outside.csv",
        "folder/../deck.csv",
        "folder//deck.csv",
        "./deck.csv",
        "deck.txt",
        "folder\\deck.csv",
    ] {
        assert!(job.register_artifact(name).is_err());
    }
}

#[cfg(unix)]
fn assert_external_paths(
    job: &mut LocalJobWorkspace,
    root: &Path,
    external: &Path,
    external_file: &Path,
    names: &[flashcards_services::document_inputs::SourceFilename],
    owner: &UserId,
    path: &Path,
) {
    #[cfg(unix)]
    {
        let saved_output = root.join("saved-output");
        fs::rename(job.output_dir(), &saved_output).unwrap();
        std::os::unix::fs::symlink(external, job.output_dir()).unwrap();
        assert!(job.register_artifact("private.csv").is_err());
        assert!(job.open_artifact(owner, "deck.csv").is_err());
        fs::remove_file(job.output_dir()).unwrap();
        fs::rename(saved_output, job.output_dir()).unwrap();
        let saved_input = root.join("saved-input");
        fs::rename(job.input_dir(), &saved_input).unwrap();
        std::os::unix::fs::symlink(external, job.input_dir()).unwrap();
        assert!(job.create_input(&names[0]).is_err());
        assert!(!external.join(names[0].as_str()).exists());
        fs::remove_file(job.input_dir()).unwrap();
        fs::rename(saved_input, job.input_dir()).unwrap();
        std::os::unix::fs::symlink(external_file, job.output_dir().join("link.csv")).unwrap();
        assert!(job.register_artifact("link.csv").is_err());
        std::os::unix::fs::symlink(external, job.output_dir().join("linked")).unwrap();
        assert!(job.register_artifact("linked/private.csv").is_err());
        fs::remove_file(path).unwrap();
        std::os::unix::fs::symlink(external_file, path).unwrap();
        assert!(job.open_artifact(owner, "deck.csv").is_err());
    }
}

#[cfg(unix)]
fn assert_workspace_permissions(
    job: &LocalJobWorkspace,
    root: &Path,
    name: &flashcards_services::document_inputs::SourceFilename,
) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for path in [root, job.input_dir(), job.output_dir()] {
            assert_eq!(
                fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o700
            );
        }
        assert_eq!(
            fs::metadata(job.input_dir().join(name.as_str()))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
}
