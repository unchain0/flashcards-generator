use flashcards_domain::identity::UserId;
use flashcards_integrations::{
    logging,
    notebooklm::{NotebookLMClient, NotebookLMError},
    notebooklm_browser::{BrowserLoginError, NotebookLMBrowserLogin},
    notebooklm_profiles::LocalNotebookLMProfiles,
};
use flashcards_services::notebooklm::{Notebook, NotebookLMGateway};
use std::{
    error::Error,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
};

type Failure = Box<dyn Error>;
type ProfileSnapshot = Vec<(PathBuf, Option<Vec<u8>>)>;

#[tokio::main]
async fn main() -> Result<(), Failure> {
    logging::initialize();
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    if let [mode, source, destination] = arguments.as_slice()
        && mode == "--copy-profile"
    {
        return copy_checked_profile(Path::new(source), Path::new(destination));
    }
    let (storage, browser, output, browser_output) = match arguments.as_slice() {
        [storage, browser] => (storage, browser, None, None),
        [storage, browser, output] => (storage, browser, Some(Path::new(output)), None),
        [storage, browser, output, browser_output] => (storage, browser, Some(Path::new(output)), Some(Path::new(browser_output))),
        _ => return Err("Provide a local storage-state file, its dedicated browser profile, optionally a new private session output file and an empty private browser-profile output directory".into()),
    };
    if let Some(output) = output {
        validate_session_output(output)?;
    }
    if let Some(output) = browser_output {
        validate_profile_destination(output)?;
    }
    let source = PathBuf::from(browser);
    let original = snapshot(&source)?;
    let temporary = tempfile::Builder::new()
        .prefix(".rust-login-qa-")
        .tempdir_in(source.parent().ok_or("Profile parent unavailable")?)?;
    let profiles = LocalNotebookLMProfiles::new(temporary.path().join("companion"))?;
    let user = UserId::try_from("0123456789abcdef0123456789abcdef".to_owned())?;
    copy_profile(&original, &profiles.browser_home(&user)?)?;
    let before = initial_notebooks(&PathBuf::from(storage)).await?;
    tracing::info!("Native browser login validation started with a private profile copy");
    let login = NotebookLMBrowserLogin::new(None)
        .login(&profiles, &user)
        .await;
    if snapshot(&source)? != original {
        return Err("Original browser profile changed during validation".into());
    }
    if let Err(error) = login {
        let stage = match &error {
            BrowserLoginError::Verification(NotebookLMError::Status(_)) => "provider_http",
            BrowserLoginError::Verification(NotebookLMError::Schema(_)) => "provider_schema",
            BrowserLoginError::Verification(_) => "session_verification",
            _ => "browser",
        };
        tracing::error!(stage, error = %error, "Native browser login validation failed");
        return Err("Native browser login validation failed".into());
    }
    let verified = NotebookLMClient::for_user(&profiles, &user).await?;
    let mut after = verified
        .list_notebooks()
        .await?
        .into_iter()
        .map(|notebook| notebook.id)
        .collect::<Vec<_>>();
    after.sort();
    compare_inventory(before, &after)?;
    if let Some(output) = browser_output {
        copy_checked_profile(&profiles.browser_home(&user)?, output)?;
    }
    if let Some(output) = output {
        let raw = profiles
            .load(&user)?
            .ok_or("Verified session was not persisted")?;
        export_session(output, &raw)?;
        tracing::info!("Verified QA session saved locally for subsequent checks");
    }
    tracing::info!(
        notebook_count = after.len(),
        "Native browser session capture, private persistence, and original profile preservation passed"
    );
    Ok(())
}

fn validate_profile_destination(root: &Path) -> Result<(), Failure> {
    if !root.is_absolute()
        || root.canonicalize()? != root
        || !fs::symlink_metadata(root)?.is_dir()
        || fs::read_dir(root)?.next().is_some()
    {
        return Err("Browser profile output must be an empty private absolute directory".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if fs::metadata(root)?.permissions().mode() & 0o077 != 0 {
            return Err("Browser profile output must be private".into());
        }
    }
    Ok(())
}

fn copy_checked_profile(source: &Path, destination: &Path) -> Result<(), Failure> {
    validate_profile_destination(destination)?;
    if destination.starts_with(source.canonicalize()?) {
        return Err("Browser profile output must be outside the source profile".into());
    }
    let original = snapshot(source)?;
    copy_profile(&original, destination)?;
    if snapshot(source)? != original {
        return Err("Original browser profile changed during copy".into());
    }
    Ok(())
}

fn validate_session_output(path: &Path) -> Result<(), Failure> {
    let parent = path.parent().ok_or("Session output parent unavailable")?;
    if !path.is_absolute()
        || path.file_name().is_none()
        || path.try_exists()?
        || parent.canonicalize()? != parent
        || !fs::symlink_metadata(parent)?.is_dir()
    {
        return Err("Session output must be a new file in a private absolute directory".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if fs::metadata(parent)?.permissions().mode() & 0o077 != 0 {
            return Err("Session output directory must be private".into());
        }
    }
    Ok(())
}

fn export_session(path: &Path, raw: &str) -> Result<(), Failure> {
    let parent = path.parent().ok_or("Session output parent unavailable")?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".session-")
        .tempfile_in(parent)?;
    temporary.write_all(raw.as_bytes())?;
    temporary.as_file().sync_all()?;
    temporary.persist_noclobber(path)?;
    #[cfg(unix)]
    fs::File::open(parent)?.sync_all()?;
    Ok(())
}

fn compare_inventory(before: Option<Vec<Notebook>>, after: &[String]) -> Result<(), Failure> {
    let Some(before) = before else { return Ok(()) };
    let mut before = before
        .into_iter()
        .map(|notebook| notebook.id)
        .collect::<Vec<_>>();
    before.sort();
    if before != after {
        return Err("Notebook inventory changed during read-only login validation".into());
    }
    tracing::info!("Existing notebook inventory preservation passed");
    Ok(())
}

async fn initial_notebooks(storage: &Path) -> Result<Option<Vec<Notebook>>, NotebookLMError> {
    let result = async {
        NotebookLMClient::from_storage_file(storage)
            .await?
            .list_notebooks()
            .await
    }
    .await;
    match result {
        Ok(notebooks) => Ok(Some(notebooks)),
        Err(NotebookLMError::Authentication) => {
            tracing::info!(
                "Stored session requires login; initial notebook inventory is unavailable"
            );
            Ok(None)
        }
        Err(error) => Err(error),
    }
}

fn snapshot(root: &Path) -> Result<ProfileSnapshot, Failure> {
    if !root.is_absolute() || !fs::symlink_metadata(root)?.is_dir() {
        return Err("Dedicated browser profile must be a regular absolute directory".into());
    }
    reject_active_browser(root)?;
    let mut pending = vec![root.to_owned()];
    let mut entries = Vec::new();
    let mut bytes = 0_u64;
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory)? {
            collect_entry(&entry?, root, &mut pending, &mut entries, &mut bytes)?;
        }
    }
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(entries)
}

fn collect_entry(
    entry: &fs::DirEntry,
    root: &Path,
    pending: &mut Vec<PathBuf>,
    entries: &mut ProfileSnapshot,
    bytes: &mut u64,
) -> Result<(), Failure> {
    let path = entry.path();
    let relative = path.strip_prefix(root)?.to_owned();
    if path.parent() == Some(root)
        && matches!(
            entry.file_name().to_str(),
            Some(
                "SingletonLock" | "SingletonCookie" | "SingletonSocket" | ".flashcards-login.lock"
            )
        )
    {
        return Ok(());
    }
    if entries.len() == 10_000 {
        return Err("Dedicated browser profile exceeds the entry limit".into());
    }
    let kind = entry.file_type()?;
    let contents = if kind.is_dir() {
        pending.push(path);
        None
    } else if kind.is_file() {
        let file = fs::File::open(path)?;
        let length = file.metadata()?.len();
        if length > 512 * 1024 * 1024 - *bytes {
            return Err("Dedicated browser profile exceeds the byte limit".into());
        }
        let mut contents = Vec::new();
        file.take(length + 1).read_to_end(&mut contents)?;
        if contents.len() as u64 != length {
            return Err("Dedicated browser profile changed during snapshot".into());
        }
        *bytes += length;
        Some(contents)
    } else {
        return Err("Dedicated browser profile contains an unsupported file".into());
    };
    entries.push((relative, contents));
    Ok(())
}

fn copy_profile(snapshot: &ProfileSnapshot, root: &Path) -> Result<(), Failure> {
    for (relative, contents) in snapshot {
        let path = root.join(relative);
        if let Some(contents) = contents {
            fs::write(&path, contents)?;
            #[cfg(unix)]
            private_permissions(&path, 0o600)?;
        } else {
            fs::create_dir(&path)?;
            #[cfg(unix)]
            private_permissions(&path, 0o700)?;
        }
    }
    Ok(())
}

#[cfg(unix)]
fn private_permissions(path: &Path, mode: u32) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
}

fn reject_active_browser(root: &Path) -> Result<(), Failure> {
    #[cfg(target_os = "linux")]
    if let Ok(lock) = fs::read_link(root.join("SingletonLock")) {
        let active = lock
            .to_str()
            .and_then(|value| value.rsplit_once('-'))
            .and_then(|(_, pid)| pid.parse::<u32>().ok())
            .is_some_and(|pid| PathBuf::from(format!("/proc/{pid}")).exists());
        if active {
            return Err("Close the dedicated NotebookLM browser before validation".into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_nested_profile_destinations_without_changing_the_source() {
        let source = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir_in(source.path()).unwrap();
        #[cfg(unix)]
        private_permissions(destination.path(), 0o700).unwrap();
        let original = snapshot(source.path()).unwrap();
        assert!(copy_checked_profile(source.path(), destination.path()).is_err());
        assert_eq!(snapshot(source.path()).unwrap(), original);
    }

    #[test]
    fn copies_private_profile_contents_and_rejects_populated_destinations() {
        let source = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        #[cfg(unix)]
        private_permissions(destination.path(), 0o700).unwrap();
        fs::create_dir(source.path().join("Default")).unwrap();
        fs::write(
            source.path().join("Default/Cookies"),
            b"synthetic cookie data",
        )
        .unwrap();
        let original = snapshot(source.path()).unwrap();
        copy_checked_profile(source.path(), destination.path()).unwrap();
        assert_eq!(snapshot(source.path()).unwrap(), original);
        assert_eq!(snapshot(destination.path()).unwrap(), original);
        fs::write(
            destination.path().join("Default/Cookies"),
            b"existing profile data",
        )
        .unwrap();
        assert!(copy_checked_profile(source.path(), destination.path()).is_err());
        assert_eq!(
            fs::read(destination.path().join("Default/Cookies")).unwrap(),
            b"existing profile data"
        );
    }
}
