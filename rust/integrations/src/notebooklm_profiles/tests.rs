use super::*;

#[cfg(unix)]
mod ancestors;

const STORAGE: &str = r#"{"cookies":[{"name":"SID","value":"synthetic-cookie","domain":".google.com","path":"/"}],"origins":[],"_meta":{"preserve":"unchanged"}}"#;

fn user(letter: &str) -> UserId {
    UserId::try_from(letter.repeat(32)).unwrap()
}

#[test]
fn isolates_users_and_preserves_existing_profile_layouts_and_browser_sessions() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let first = user("a");
    let second = user("b");
    assert_eq!(profiles.load(&first).unwrap(), None);
    profiles.save(&first, STORAGE).unwrap();
    assert_eq!(profiles.load(&first).unwrap().as_deref(), Some(STORAGE));
    assert_eq!(profiles.load(&second).unwrap(), None);
    let first_home = profiles.home(&first).unwrap();
    let second_home = profiles.home(&second).unwrap();
    assert_ne!(first_home, second_home);
    let browser = profiles.browser_home(&first).unwrap();
    assert_eq!(browser, first_home.join("profiles/default/browser_profile"));
    fs::write(browser.join("existing-session"), "preserve browser").unwrap();
    profiles.save(&first, STORAGE).unwrap();
    assert_eq!(
        fs::read_to_string(browser.join("existing-session")).unwrap(),
        "preserve browser"
    );
    let newer = STORAGE.replace("synthetic-cookie", "renewed-cookie");
    assert_legacy_profile(&profiles, &second, &second_home, &newer);
    fs::write(
        first_home.join("config.json"),
        r#"{"default_profile":"Study é"}"#,
    )
    .unwrap();
    assert_eq!(profiles.load(&first).unwrap(), None);
    profiles.save(&first, &newer).unwrap();
    assert_eq!(
        profiles.load(&first).unwrap().as_deref(),
        Some(newer.as_str())
    );
    assert_eq!(
        profiles.browser_home(&first).unwrap(),
        first_home.join("profiles/Study é/browser_profile")
    );
    assert_eq!(
        fs::read_to_string(first_home.join("profiles/default/storage_state.json")).unwrap(),
        STORAGE
    );
    #[cfg(unix)]
    assert_private_profile(&profiles, &first_home, &browser);
    assert!(
        fs::read_dir(first_home.join("profiles/Study é"))
            .unwrap()
            .all(|entry| !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".tmp"))
    );
}

fn assert_legacy_profile(
    profiles: &LocalNotebookLMProfiles,
    second: &UserId,
    second_home: &Path,
    newer: &str,
) {
    let legacy = second_home.join("storage_state.json");
    fs::write(&legacy, STORAGE).unwrap();
    let old_browser = second_home.join("browser_profile");
    fs::create_dir(&old_browser).unwrap();
    fs::write(
        old_browser.join("existing-session"),
        "preserve legacy browser",
    )
    .unwrap();
    assert_eq!(profiles.load(second).unwrap().as_deref(), Some(STORAGE));
    assert_eq!(profiles.browser_home(second).unwrap(), old_browser);
    profiles.save(second, newer).unwrap();
    assert_eq!(fs::read_to_string(&legacy).unwrap(), newer);
    assert!(
        !second_home
            .join("profiles/default/storage_state.json")
            .exists()
    );
    let canonical = second_home.join("profiles/default/storage_state.json");
    fs::write(&canonical, STORAGE).unwrap();
    assert_eq!(profiles.load(second).unwrap().as_deref(), Some(STORAGE));
    assert_eq!(fs::read_to_string(&legacy).unwrap(), newer);
}

#[cfg(unix)]
fn assert_private_profile(profiles: &LocalNotebookLMProfiles, first_home: &Path, browser: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for path in [
            profiles.data_dir.as_path(),
            first_home,
            &first_home.join("profiles/Study é"),
            browser,
        ] {
            assert_eq!(
                fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o700
            );
        }
        assert_eq!(
            fs::metadata(first_home.join("profiles/Study é/storage_state.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
}

#[test]
fn rejects_unsafe_profile_names_and_invalid_credentials_without_overwriting_originals() {
    let directory = tempfile::tempdir().unwrap();
    assert!(LocalNotebookLMProfiles::new(PathBuf::from("relative")).is_err());
    #[cfg(unix)]
    assert!(LocalNotebookLMProfiles::new(PathBuf::from("/")).is_err());
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let identity = user("a");
    profiles.save(&identity, STORAGE).unwrap();
    let home = profiles.home(&identity).unwrap();
    for invalid in [
        "",
        "invalid JSON",
        r#"{"cookies":"private-cookie-value"}"#,
        r#"{"cookies":[]}"#,
    ] {
        let error = profiles.save(&identity, invalid).unwrap_err();
        assert!(!format!("{error:?}").contains("private-cookie-value"));
        assert_eq!(profiles.load(&identity).unwrap().as_deref(), Some(STORAGE));
    }
    assert!(matches!(
        profiles.save(
            &identity,
            &" ".repeat(usize::try_from(MAX_STORAGE_BYTES).unwrap() + 1)
        ),
        Err(ProfileError::TooLarge)
    ));
    for name in [
        "../other-user",
        "/tmp/outside",
        "..",
        ".",
        "profiles/nested",
        "profiles\\nested",
        "bad\nprofile",
    ] {
        let config = serde_json::json!({"default_profile":name}).to_string();
        fs::write(home.join("config.json"), config).unwrap();
        assert!(matches!(
            profiles.load(&identity),
            Err(ProfileError::InvalidConfig)
        ));
        assert!(profiles.save(&identity, STORAGE).is_err());
    }
    fs::write(home.join("config.json"), "invalid JSON").unwrap();
    assert!(matches!(
        profiles.load(&identity),
        Err(ProfileError::InvalidConfig)
    ));
    fs::write(home.join("config.json"), r#"{"default_profile":"default"}"#).unwrap();
    assert_eq!(profiles.load(&identity).unwrap().as_deref(), Some(STORAGE));
    let storage = home.join("profiles/default/storage_state.json");
    fs::File::create(&storage)
        .unwrap()
        .set_len(MAX_STORAGE_BYTES + 1)
        .unwrap();
    assert!(matches!(
        profiles.load(&identity),
        Err(ProfileError::TooLarge)
    ));
}

#[cfg(unix)]
#[test]
fn rejects_symlinks_at_each_profile_boundary_without_changing_external_data() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let directory = tempfile::tempdir().unwrap();
    let outside = directory.path().join("outside");
    fs::create_dir(&outside).unwrap();
    fs::set_permissions(&outside, fs::Permissions::from_mode(0o755)).unwrap();
    let original = outside.join("original.json");
    fs::write(&original, STORAGE).unwrap();
    let linked_data = directory.path().join("linked-companion");
    symlink(&outside, &linked_data).unwrap();
    let linked = LocalNotebookLMProfiles::new(linked_data).unwrap();
    assert!(linked.load(&user("a")).is_err());
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let identity = user("a");
    let home = profiles.home(&identity).unwrap();
    let path = home.join("profiles/default");
    fs::create_dir_all(&path).unwrap();
    symlink(&original, path.join("storage_state.json")).unwrap();
    assert!(matches!(
        profiles.load(&identity),
        Err(ProfileError::InvalidPath)
    ));
    assert!(profiles.save(&identity, STORAGE).is_err());
    let second = user("b");
    let second_home = profiles.home(&second).unwrap();
    symlink(&outside, second_home.join("profiles")).unwrap();
    assert!(profiles.load(&second).is_err());
    let third = user("c");
    symlink(
        &outside,
        profiles.data_dir.join("profiles").join(third.as_str()),
    )
    .unwrap();
    assert!(profiles.load(&third).is_err());
    let fourth = user("d");
    let fourth_home = profiles.home(&fourth).unwrap();
    symlink(&original, fourth_home.join("config.json")).unwrap();
    assert!(profiles.load(&fourth).is_err());
    let fifth = user("e");
    let fifth_home = profiles.home(&fifth).unwrap();
    symlink(&outside, fifth_home.join("browser_profile")).unwrap();
    assert!(profiles.browser_home(&fifth).is_err());
    assert_eq!(fs::read_to_string(&original).unwrap(), STORAGE);
    assert_eq!(
        fs::metadata(&outside).unwrap().permissions().mode() & 0o777,
        0o755
    );
    assert_eq!(fs::read_dir(&outside).unwrap().count(), 1);
}

#[test]
fn rejects_corrupt_stored_credentials_and_keeps_selected_accounts_separate() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let identity = user("a");
    profiles.save(&identity, STORAGE).unwrap();
    let home = profiles.home(&identity).unwrap();
    let storage = home.join("profiles/default/storage_state.json");
    fs::write(&storage, "invalid stored credentials").unwrap();
    assert!(matches!(
        profiles.load(&identity),
        Err(ProfileError::InvalidCredentials)
    ));
    profiles.save(&identity, STORAGE).unwrap();

    let legacy = home.join("browser_profile");
    fs::create_dir(&legacy).unwrap();
    fs::write(legacy.join("account-marker"), "legacy-account").unwrap();
    fs::write(
        home.join("config.json"),
        r#"{"default_profile":"Other Account"}"#,
    )
    .unwrap();
    assert_eq!(profiles.load(&identity).unwrap(), None);
    assert_eq!(
        profiles.browser_home(&identity).unwrap(),
        home.join("profiles/Other Account/browser_profile")
    );
    assert_eq!(
        fs::read_to_string(legacy.join("account-marker")).unwrap(),
        "legacy-account"
    );
    assert_eq!(fs::read_to_string(storage).unwrap(), STORAGE);
}

#[test]
fn profile_diagnostics_hide_sensitive_causes_and_preserve_error_chains() {
    let error = ProfileError::from(io::Error::other("private-profile-value"));
    assert_ne!(error.to_string(), "");
    assert!(!error.to_string().contains("private-profile-value"));
    assert_eq!(error.source().unwrap().to_string(), "private-profile-value");
    for error in [
        ProfileError::InvalidPath,
        ProfileError::InvalidConfig,
        ProfileError::InvalidCredentials,
        ProfileError::TooLarge,
        ProfileError::Busy,
    ] {
        assert_ne!(error.to_string(), "");
        assert!(!error.to_string().contains("private-profile-value"));
        assert!(error.source().is_none());
    }
}

#[cfg(unix)]
#[test]
fn rejects_a_lock_replaced_between_metadata_and_open() {
    let first = tempfile::NamedTempFile::new().unwrap();
    let second = tempfile::NamedTempFile::new().unwrap();
    let original = first.as_file().metadata().unwrap();
    let replaced = second.as_file().metadata().unwrap();
    assert!(matches!(
        validate_opened_lock(&original, &replaced),
        Err(ProfileError::InvalidPath)
    ));
    assert!(validate_opened_lock(&original, &original).is_ok());
}

#[cfg(target_os = "linux")]
#[test]
fn rejects_lock_metadata_from_a_different_device() {
    use std::os::unix::fs::MetadataExt;
    let original = tempfile::NamedTempFile::new().unwrap();
    let replacement = File::from(
        rustix::fs::memfd_create("synthetic-profile-lock", rustix::fs::MemfdFlags::CLOEXEC)
            .unwrap(),
    );
    let original_metadata = original.as_file().metadata().unwrap();
    let replaced_metadata = replacement.metadata().unwrap();
    assert_ne!(original_metadata.dev(), replaced_metadata.dev());
    assert!(matches!(
        validate_opened_lock(&original_metadata, &replaced_metadata),
        Err(ProfileError::InvalidPath)
    ));
    assert_eq!(original.as_file().metadata().unwrap().len(), 0);
}

#[cfg(target_os = "linux")]
#[test]
fn rejects_storage_paths_exceeding_the_kernel_limit_without_touching_other_profiles() {
    let directory = tempfile::tempdir().unwrap();
    let preserved = directory.path().join("existing-profile.json");
    fs::write(&preserved, STORAGE).unwrap();
    let mut root = directory.path().to_owned();
    while root.as_os_str().len() < 3890 {
        root.push("x".repeat(128));
    }
    root.push("x".repeat(4020 - root.as_os_str().len() - 1));
    assert_eq!(root.as_os_str().len(), 4020);
    let profiles = LocalNotebookLMProfiles::new(root).unwrap();
    let identity = user("a");
    for error in [
        profiles.load(&identity).unwrap_err(),
        profiles.save(&identity, STORAGE).unwrap_err(),
    ] {
        let ProfileError::Io(cause) = error else {
            panic!("Overlong storage paths must retain their filesystem cause")
        };
        assert_eq!(
            cause.raw_os_error(),
            Some(rustix::io::Errno::NAMETOOLONG.raw_os_error())
        );
    }
    assert_eq!(fs::read_to_string(&preserved).unwrap(), STORAGE);
}

#[test]
fn default_profile_fallbacks_reject_unsafe_legacy_storage_without_touching_other_accounts() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let identity = user("a");
    profiles.save(&identity, STORAGE).unwrap();
    let home = profiles.home(&identity).unwrap();
    for config in [
        "{}",
        "null",
        r#"{"default_profile":""}"#,
        r#"{"default_profile":3}"#,
    ] {
        fs::write(home.join("config.json"), config).unwrap();
        assert_eq!(profiles.load(&identity).unwrap().as_deref(), Some(STORAGE));
    }
    let canonical = home.join("profiles/default/storage_state.json");
    fs::remove_file(&canonical).unwrap();
    let legacy = home.join("storage_state.json");
    fs::create_dir(&legacy).unwrap();
    fs::write(legacy.join("original"), "preserve legacy contents").unwrap();
    assert!(matches!(
        profiles.load(&identity),
        Err(ProfileError::InvalidPath)
    ));
    assert!(matches!(
        profiles.save(&identity, STORAGE),
        Err(ProfileError::InvalidPath)
    ));
    assert_eq!(
        fs::read_to_string(legacy.join("original")).unwrap(),
        "preserve legacy contents"
    );
    assert!(!canonical.exists());
    let second = user("b");
    profiles.save(&second, STORAGE).unwrap();
    assert_eq!(profiles.load(&second).unwrap().as_deref(), Some(STORAGE));
}

#[test]
fn private_profile_reads_retain_filesystem_and_encoding_causes_without_exposing_contents() {
    let directory = tempfile::tempdir().unwrap();
    let file = directory.path().join("private-profile");
    fs::write(&file, [0xff, 0xfe]).unwrap();
    for (path, expected) in [
        (file.clone(), io::ErrorKind::InvalidData),
        (file.join("config.json"), io::ErrorKind::NotADirectory),
    ] {
        let error = read_private_file(&path, 1024).unwrap_err();
        assert_eq!(
            error.to_string(),
            "Local NotebookLM profile operation failed"
        );
        let ProfileError::Io(cause) = error else {
            panic!("Expected an I/O cause");
        };
        assert_eq!(cause.kind(), expected);
    }
    assert_eq!(fs::read(file).unwrap(), [0xff, 0xfe]);
}

#[test]
fn releasing_a_profile_lock_does_not_wait_for_inherited_descriptors() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let identity = user("a");
    let (_, held) = profiles.lock_browser(&identity).unwrap();
    let inherited = held.file.try_clone().unwrap();
    drop(held);
    let reacquired = profiles.lock_browser(&identity);
    assert!(reacquired.is_ok());
    drop(inherited);
}

#[tokio::test]
async fn cancelling_the_lock_owner_releases_the_profile_with_inherited_descriptors_open() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = LocalNotebookLMProfiles::new(directory.path().join("companion")).unwrap();
    let identity = user("a");
    let (_, held) = profiles.lock_browser(&identity).unwrap();
    let inherited = held.file.try_clone().unwrap();
    let entered = std::sync::Arc::new(tokio::sync::Notify::new());
    let notify = entered.clone();
    let owner = tokio::spawn(async move {
        let guard = held;
        notify.notify_one();
        std::future::pending::<()>().await;
        drop(guard);
    });
    tokio::time::timeout(std::time::Duration::from_secs(5), entered.notified())
        .await
        .unwrap();
    owner.abort();
    assert!(owner.await.unwrap_err().is_cancelled());
    assert!(profiles.lock_browser(&identity).is_ok());
    drop(inherited);
}
