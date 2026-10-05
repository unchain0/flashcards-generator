use crate::{document_files::readonly_file_options, notebooklm::validate_storage_state};
use flashcards_domain::identity::UserId;
use serde_json::Value;
use std::{
    error::Error,
    fmt,
    fs::{self, File},
    io::{self, Read, Write},
    path::{Component, Path, PathBuf},
};

const MAX_STORAGE_BYTES: u64 = 4 * 1024 * 1024;
#[cfg(any(target_os = "linux", target_os = "android"))]
mod directories;
#[cfg(test)]
mod tests;

#[derive(Debug)]
pub enum ProfileError {
    Io(io::Error),
    InvalidPath,
    InvalidConfig,
    InvalidCredentials,
    TooLarge,
    Busy,
}

impl fmt::Display for ProfileError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Io(_) => "Local NotebookLM profile operation failed",
            Self::InvalidPath => "Local NotebookLM profile path is unsafe",
            Self::InvalidConfig => "Local NotebookLM profile configuration is invalid",
            Self::InvalidCredentials => "Local NotebookLM credentials are invalid",
            Self::TooLarge => "Local NotebookLM profile exceeds size limit",
            Self::Busy => "Local NotebookLM browser profile is already in use",
        })
    }
}

impl Error for ProfileError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            _ => None,
        }
    }
}

impl From<io::Error> for ProfileError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub(crate) struct BrowserProfileLock {
    file: File,
}

impl Drop for BrowserProfileLock {
    fn drop(&mut self) {
        if self.file.unlock().is_err() {
            tracing::warn!("Local NotebookLM browser profile lock could not be released");
        }
    }
}

#[derive(Clone)]
pub struct LocalNotebookLMProfiles {
    data_dir: PathBuf,
}

impl LocalNotebookLMProfiles {
    /// # Errors
    /// Rejects unsafe data directory boundaries and propagates private directory creation failures.
    pub fn new(data_dir: PathBuf) -> Result<Self, ProfileError> {
        if !data_dir.is_absolute()
            || data_dir.file_name().is_none()
            || data_dir
                .components()
                .any(|part| part == Component::ParentDir)
        {
            return Err(ProfileError::InvalidPath);
        }
        Ok(Self { data_dir })
    }

    /// # Errors
    /// Rejects unsafe profile boundaries and propagates profile directory creation failures.
    pub fn home(&self, user: &UserId) -> Result<PathBuf, ProfileError> {
        private_directory(&self.data_dir)?;
        let profiles = self.data_dir.join("profiles");
        private_directory(&profiles)?;
        let home = profiles.join(user.as_str());
        private_directory(&home)?;
        Ok(home)
    }

    fn selected_profile(home: &Path) -> Result<String, ProfileError> {
        let Some(raw) = read_private_file(&home.join("config.json"), 65_536)? else {
            return Ok("default".to_owned());
        };
        let config: Value = serde_json::from_str(&raw).map_err(|_| ProfileError::InvalidConfig)?;
        let name = config
            .get("default_profile")
            .and_then(Value::as_str)
            .filter(|name| !name.is_empty())
            .unwrap_or("default");
        let mut parts = Path::new(name).components();
        if !matches!(parts.next(), Some(Component::Normal(_)))
            || parts.next().is_some()
            || name.contains(['/', '\\'])
            || name.chars().any(char::is_control)
        {
            return Err(ProfileError::InvalidConfig);
        }
        Ok(name.to_owned())
    }

    fn storage_path(&self, user: &UserId) -> Result<PathBuf, ProfileError> {
        let home = self.home(user)?;
        let name = Self::selected_profile(&home)?;
        let profiles = home.join("profiles");
        private_directory(&profiles)?;
        let selected = profiles.join(&name);
        private_directory(&selected)?;
        let storage = selected.join("storage_state.json");
        match fs::symlink_metadata(&storage) {
            Ok(metadata) if metadata.is_file() => Ok(storage),
            Ok(_) => Err(ProfileError::InvalidPath),
            Err(error) if error.kind() == io::ErrorKind::NotFound && name == "default" => {
                let legacy = home.join("storage_state.json");
                match fs::symlink_metadata(&legacy) {
                    Ok(metadata) if metadata.is_file() => Ok(legacy),
                    Ok(_) => Err(ProfileError::InvalidPath),
                    Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(storage),
                    Err(error) => Err(error.into()),
                }
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(storage),
            Err(error) => Err(error.into()),
        }
    }

    /// # Errors
    /// Rejects unsafe or excessive credential files and propagates bounded file reading failures.
    pub fn load(&self, user: &UserId) -> Result<Option<String>, ProfileError> {
        let raw = read_private_file(&self.storage_path(user)?, MAX_STORAGE_BYTES)?;
        if let Some(raw) = &raw {
            validate_storage_state(raw).map_err(|_| ProfileError::InvalidCredentials)?;
        }
        Ok(raw)
    }

    /// # Errors
    /// Rejects invalid or excessive credentials and unsafe boundaries; propagates atomic persistence failures.
    pub fn save(&self, user: &UserId, raw: &str) -> Result<(), ProfileError> {
        if raw.len() as u64 > MAX_STORAGE_BYTES {
            return Err(ProfileError::TooLarge);
        }
        validate_storage_state(raw).map_err(|_| ProfileError::InvalidCredentials)?;
        let path = self.storage_path(user)?;
        let parent = path.parent().ok_or(ProfileError::InvalidPath)?;
        let mut temporary = tempfile::Builder::new()
            .prefix(".storage-")
            .suffix(".tmp")
            .tempfile_in(parent)?;
        temporary.write_all(raw.as_bytes())?;
        temporary.as_file().sync_all()?;
        temporary
            .persist(&path)
            .map_err(|error| ProfileError::Io(error.error))?;
        #[cfg(unix)]
        File::open(parent)?.sync_all()?;
        Ok(())
    }

    /// # Errors
    /// Rejects unsafe browser profile boundaries and propagates directory creation failures.
    pub fn browser_home(&self, user: &UserId) -> Result<PathBuf, ProfileError> {
        let storage = self.storage_path(user)?;
        let home = self.home(user)?;
        let canonical = storage
            .parent()
            .ok_or(ProfileError::InvalidPath)?
            .join("browser_profile");
        let legacy = home.join("browser_profile");
        let selected = if !canonical.try_exists()?
            && Self::selected_profile(&home)? == "default"
            && legacy.try_exists()?
        {
            legacy
        } else {
            canonical
        };
        private_directory(&selected)?;
        Ok(selected)
    }

    pub(crate) fn lock_browser(
        &self,
        user: &UserId,
    ) -> Result<(PathBuf, BrowserProfileLock), ProfileError> {
        let home = self.browser_home(user)?;
        let path = home.join(".flashcards-login.lock");
        let file = match File::create_new(&path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                read_private_file(&path, 0)?;
                readonly_file_options().write(true).open(&path)?
            }
            Err(error) => return Err(error.into()),
        };
        let metadata = fs::symlink_metadata(&path)?;
        if !metadata.is_file() {
            return Err(ProfileError::InvalidPath);
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let opened = file.metadata()?;
            validate_opened_lock(&metadata, &opened)?;
            file.set_permissions(fs::Permissions::from_mode(0o600))?;
        }
        file.try_lock().map_err(|error| match error {
            std::fs::TryLockError::WouldBlock => ProfileError::Busy,
            std::fs::TryLockError::Error(error) => ProfileError::Io(error),
        })?;
        Ok((home, BrowserProfileLock { file }))
    }
}

#[cfg(unix)]
fn validate_opened_lock(
    metadata: &fs::Metadata,
    opened: &fs::Metadata,
) -> Result<(), ProfileError> {
    use std::os::unix::fs::MetadataExt;
    if metadata.dev() != opened.dev() || metadata.ino() != opened.ino() {
        return Err(ProfileError::InvalidPath);
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn private_directory(path: &Path) -> Result<(), ProfileError> {
    directories::create(path)
}

#[cfg(not(any(target_os = "linux", target_os = "android")))]
fn private_directory(path: &Path) -> Result<(), ProfileError> {
    for ancestor in path.ancestors() {
        validate_ancestor(ancestor)?;
    }
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)?;
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() {
        return Err(ProfileError::InvalidPath);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        let directory = File::open(path)?;
        let opened = directory.metadata()?;
        if opened.dev() != metadata.dev() || opened.ino() != metadata.ino() {
            return Err(ProfileError::InvalidPath);
        }
        directory.set_permissions(fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

#[cfg(not(any(target_os = "linux", target_os = "android")))]
fn validate_ancestor(path: &Path) -> Result<(), ProfileError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if !metadata.is_dir() || metadata.file_type().is_symlink() {
                return Err(ProfileError::InvalidPath);
            }
            Ok(())
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

pub(crate) fn read_private_file(path: &Path, maximum: u64) -> Result<Option<String>, ProfileError> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if !metadata.is_file() {
        return Err(ProfileError::InvalidPath);
    }
    if metadata.len() > maximum {
        return Err(ProfileError::TooLarge);
    }
    let file = readonly_file_options().open(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        let opened = file.metadata()?;
        if opened.dev() != metadata.dev() || opened.ino() != metadata.ino() {
            return Err(ProfileError::InvalidPath);
        }
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    let mut raw = String::new();
    file.take(maximum + 1).read_to_string(&mut raw)?;
    if raw.len() as u64 > maximum {
        return Err(ProfileError::TooLarge);
    }
    Ok(Some(raw))
}
