use super::*;
use std::{
    fs,
    os::unix::fs::{PermissionsExt, symlink},
};

#[test]
fn invalid_directory_roots_cannot_change_root_permissions_or_create_external_entries() {
    let root = tempfile::tempdir().unwrap();
    let root_mode = fs::metadata("/").unwrap().permissions().mode();
    for path in [
        Path::new("relative").to_owned(),
        Path::new("/").to_owned(),
        root.path().join("..").join("untrusted-profile"),
    ] {
        assert!(matches!(create(&path), Err(ProfileError::InvalidPath)));
    }
    assert_eq!(fs::metadata("/").unwrap().permissions().mode(), root_mode);
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
}

#[test]
fn renamed_ancestors_cannot_redirect_descriptor_relative_creation() {
    let root = tempfile::tempdir().unwrap();
    let parent = root.path().join("parent");
    let moved = root.path().join("moved");
    let outside = root.path().join("outside");
    fs::create_dir(&parent).unwrap();
    fs::create_dir(&outside).unwrap();
    fs::set_permissions(&outside, fs::Permissions::from_mode(0o755)).unwrap();
    fs::write(outside.join("original"), "preserve external data").unwrap();
    let held = open(&parent, DIRECTORY_FLAGS, Mode::empty()).unwrap();
    fs::rename(&parent, &moved).unwrap();
    symlink(&outside, &parent).unwrap();
    let child = create_child(&held, OsStr::new("profile")).unwrap();
    set_private_permissions(&child).unwrap();
    assert!(moved.join("profile").is_dir());
    assert_eq!(
        fs::metadata(moved.join("profile"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o700
    );
    assert_eq!(fs::read_dir(&outside).unwrap().count(), 1);
    assert_eq!(
        fs::read_to_string(outside.join("original")).unwrap(),
        "preserve external data"
    );
    assert_eq!(
        fs::metadata(&outside).unwrap().permissions().mode() & 0o777,
        0o755
    );
}

#[test]
fn concurrently_created_children_are_reopened_without_following_links() {
    let root = tempfile::tempdir().unwrap();
    let parent = open(root.path(), DIRECTORY_FLAGS, Mode::empty()).unwrap();
    fs::create_dir(root.path().join("existing")).unwrap();
    assert!(create_child(&parent, OsStr::new("existing")).is_ok());
    let outside = tempfile::tempdir().unwrap();
    symlink(outside.path(), root.path().join("aliased")).unwrap();
    assert!(matches!(
        create_child(&parent, OsStr::new("aliased")),
        Err(ProfileError::InvalidPath)
    ));
    fs::write(root.path().join("file"), "unchanged").unwrap();
    assert!(matches!(
        create_child(&parent, OsStr::new("file")),
        Err(ProfileError::InvalidPath)
    ));
    assert_eq!(
        fs::read_to_string(root.path().join("file")).unwrap(),
        "unchanged"
    );
    assert_eq!(fs::read_dir(outside.path()).unwrap().count(), 0);
}

#[test]
fn invalid_creation_arguments_preserve_io_causes_without_private_diagnostics() {
    let root = tempfile::tempdir().unwrap();
    let parent = open(root.path(), DIRECTORY_FLAGS, Mode::empty()).unwrap();
    let error = create_child(&parent, OsStr::new("private-profile\0")).unwrap_err();
    assert!(matches!(error, ProfileError::Io(_)));
    assert!(!error.to_string().contains("private-profile"));
    assert!(std::error::Error::source(&error).is_some());
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
}
