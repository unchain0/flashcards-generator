use super::*;
use std::os::unix::fs::{PermissionsExt, symlink};

#[test]
fn traverses_execute_only_ancestors_without_changing_their_permissions() {
    let directory = tempfile::tempdir().unwrap();
    let ancestor = directory.path().join("ancestor");
    fs::create_dir(&ancestor).unwrap();
    fs::create_dir(ancestor.join("companion")).unwrap();
    fs::set_permissions(&ancestor, fs::Permissions::from_mode(0o111)).unwrap();
    let profiles = LocalNotebookLMProfiles::new(ancestor.join("companion")).unwrap();
    let identity = user("a");
    let result = profiles.save(&identity, STORAGE);
    let mode = fs::metadata(&ancestor).unwrap().permissions().mode() & 0o777;
    fs::set_permissions(&ancestor, fs::Permissions::from_mode(0o700)).unwrap();
    assert!(result.is_ok(), "{result:?}");
    assert_eq!(mode, 0o111);
    assert_eq!(profiles.load(&identity).unwrap().as_deref(), Some(STORAGE));
}

#[test]
fn rejects_symlinked_ancestors_before_creating_or_modifying_profiles() {
    for operation in ["home", "load", "save", "browser", "lock"] {
        let directory = tempfile::tempdir().unwrap();
        let outside = directory.path().join("outside");
        fs::create_dir(&outside).unwrap();
        fs::set_permissions(&outside, fs::Permissions::from_mode(0o755)).unwrap();
        let original = outside.join("original.json");
        fs::write(&original, STORAGE).unwrap();
        fs::set_permissions(&original, fs::Permissions::from_mode(0o644)).unwrap();
        let alias = directory.path().join("alias");
        symlink(&outside, &alias).unwrap();
        let profiles = LocalNotebookLMProfiles::new(alias.join("companion")).unwrap();
        let identity = user("a");
        let result = match operation {
            "home" => profiles.home(&identity).map(|_| ()),
            "load" => profiles.load(&identity).map(|_| ()),
            "save" => profiles.save(&identity, STORAGE),
            "browser" => profiles.browser_home(&identity).map(|_| ()),
            _ => profiles.lock_browser(&identity).map(|_| ()),
        };
        assert!(
            matches!(result, Err(ProfileError::InvalidPath)),
            "{operation}: {result:?}"
        );
        assert_eq!(fs::read_to_string(&original).unwrap(), STORAGE);
        assert_eq!(fs::read_dir(&outside).unwrap().count(), 1);
        assert_eq!(
            fs::metadata(&outside).unwrap().permissions().mode() & 0o777,
            0o755
        );
        assert_eq!(
            fs::metadata(&original).unwrap().permissions().mode() & 0o777,
            0o644
        );
    }
}
