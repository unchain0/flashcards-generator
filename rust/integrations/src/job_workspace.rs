use crate::document_files::{MAX_DOCUMENT_BYTES, open_regular, private_tempdir};
use flashcards_domain::identity::UserId;
use flashcards_services::document_inputs::{MAX_JOB_FILES, SourceFilename};
use std::{
    collections::BTreeMap,
    error::Error,
    fmt,
    fs::{self, File, OpenOptions},
    io,
    path::{Path, PathBuf},
};

#[derive(Debug)]
pub enum WorkspaceError {
    InvalidInput,
    InvalidArtifact,
    Io(io::Error),
    Entropy(getrandom::Error),
}
impl fmt::Display for WorkspaceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidInput => "Invalid local job inputs",
            Self::InvalidArtifact => "Invalid local CSV artifact",
            Self::Io(_) => "Local job workspace operation failed",
            Self::Entropy(_) => "Secure job identity unavailable",
        })
    }
}
impl Error for WorkspaceError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Entropy(error) => Some(error),
            _ => None,
        }
    }
}
impl From<io::Error> for WorkspaceError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub struct LocalJobWorkspace {
    id: String,
    owner: UserId,
    directory: tempfile::TempDir,
    input_dir: PathBuf,
    output_dir: PathBuf,
    filenames: Vec<SourceFilename>,
    artifacts: BTreeMap<String, PathBuf>,
}
impl LocalJobWorkspace {
    /// # Errors
    /// Rejects invalid file lists and propagates entropy or private directory creation failures.
    pub fn new(owner: UserId, filenames: Vec<SourceFilename>) -> Result<Self, WorkspaceError> {
        if filenames.is_empty() || filenames.len() > MAX_JOB_FILES {
            return Err(WorkspaceError::InvalidInput);
        }
        let unique = filenames
            .iter()
            .map(SourceFilename::as_str)
            .collect::<std::collections::BTreeSet<_>>();
        if unique.len() != filenames.len() {
            return Err(WorkspaceError::InvalidInput);
        }
        let mut bytes = [0_u8; 16];
        getrandom::fill(&mut bytes).map_err(WorkspaceError::Entropy)?;
        let id = crate::hex_encoding::lowercase_hex(&bytes);
        let directory = private_tempdir("flashcards-companion-", None)?;
        let input_dir = directory.path().join("input");
        let output_dir = directory.path().join("output");
        for path in [&input_dir, &output_dir] {
            create_private_directory(path)?;
        }
        Ok(Self {
            id,
            owner,
            directory,
            input_dir,
            output_dir,
            filenames,
            artifacts: BTreeMap::new(),
        })
    }

    #[must_use]
    pub fn id(&self) -> &str {
        &self.id
    }
    #[must_use]
    pub fn owner(&self) -> &UserId {
        &self.owner
    }
    #[must_use]
    pub fn input_dir(&self) -> &Path {
        &self.input_dir
    }
    #[must_use]
    pub fn output_dir(&self) -> &Path {
        &self.output_dir
    }
    #[must_use]
    pub fn filenames(&self) -> &[SourceFilename] {
        &self.filenames
    }
    pub fn artifact_names(&self) -> impl Iterator<Item = &str> {
        self.artifacts.keys().map(String::as_str)
    }

    /// # Errors
    /// Rejects missing, replaced, or unsafe output directories.
    pub fn validate_output_dir(&self) -> Result<(), WorkspaceError> {
        validate_directory(self.directory.path())?;
        validate_directory(&self.output_dir)
    }

    pub(crate) fn export_path(&self, name: &str) -> Result<PathBuf, WorkspaceError> {
        self.validate_output_dir()?;
        artifact_path(&self.output_dir, name, true)
    }

    /// # Errors
    /// Rejects unregistered filenames and propagates confined file creation failures.
    pub fn create_input(&self, filename: &SourceFilename) -> Result<File, WorkspaceError> {
        if !self.filenames.contains(filename) {
            return Err(WorkspaceError::InvalidInput);
        }
        validate_directory(self.directory.path())?;
        validate_directory(&self.input_dir)?;
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        Ok(options.open(self.input_dir.join(filename.as_str()))?)
    }

    /// # Errors
    /// Rejects unsafe artifact names and missing, replaced, or unsafe output files.
    pub fn register_artifact(&mut self, name: &str) -> Result<(), WorkspaceError> {
        validate_directory(self.directory.path())?;
        let path = artifact_path(&self.output_dir, name, false)?;
        let file = open_regular(&path)?;
        if file.metadata()?.len() > MAX_DOCUMENT_BYTES {
            return Err(WorkspaceError::InvalidArtifact);
        }
        self.artifacts.insert(name.to_owned(), path);
        Ok(())
    }

    /// # Errors
    /// Propagates output boundary validation and confined artifact access failures.
    pub fn open_artifact(
        &self,
        owner: &UserId,
        name: &str,
    ) -> Result<Option<File>, WorkspaceError> {
        if &self.owner != owner || !self.artifacts.contains_key(name) {
            return Ok(None);
        }
        validate_directory(self.directory.path())?;
        let path = artifact_path(&self.output_dir, name, false)?;
        let file = open_regular(&path)?;
        if file.metadata()?.len() > MAX_DOCUMENT_BYTES {
            return Err(WorkspaceError::InvalidArtifact);
        }
        Ok(Some(file))
    }

    /// # Errors
    /// Rejects unsafe directory boundaries and propagates source cleanup failures.
    pub fn remove_inputs(&self) -> Result<(), WorkspaceError> {
        validate_directory(self.directory.path())?;
        match fs::remove_dir_all(&self.input_dir) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(WorkspaceError::Io(error)),
        }
    }

    /// # Errors
    /// Propagates temporary workspace removal failures.
    pub fn close(self) -> Result<(), WorkspaceError> {
        self.directory.close().map_err(WorkspaceError::Io)
    }
}

fn create_private_directory(path: &Path) -> io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)
}

fn artifact_path(root: &Path, name: &str, allow_new: bool) -> Result<PathBuf, WorkspaceError> {
    if name.is_empty()
        || name.len() > 512
        || name.contains(['\\', '\0'])
        || Path::new(name)
            .components()
            .any(|part| !matches!(part, std::path::Component::Normal(_)))
        || name
            .split('/')
            .any(|part| part.is_empty() || matches!(part, "." | ".."))
        || Path::new(name)
            .extension()
            .and_then(|value| value.to_str())
            .is_none_or(|value| !value.eq_ignore_ascii_case("csv"))
    {
        return Err(WorkspaceError::InvalidArtifact);
    }
    validate_directory(root)?;
    let mut path = root.to_owned();
    let components = name.split('/').collect::<Vec<_>>();
    for (index, component) in components.iter().enumerate() {
        path.push(component);
        let last = index + 1 == components.len();
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if allow_new && last && error.kind() == io::ErrorKind::NotFound => {
                return Ok(path);
            }
            Err(error) => return Err(error.into()),
        };
        if metadata.file_type().is_symlink()
            || (last && !metadata.is_file())
            || (!last && !metadata.is_dir())
        {
            return Err(WorkspaceError::InvalidArtifact);
        }
    }
    Ok(path)
}

fn validate_directory(path: &Path) -> Result<(), WorkspaceError> {
    if !fs::symlink_metadata(path)?.is_dir() {
        return Err(WorkspaceError::InvalidArtifact);
    }
    Ok(())
}

#[cfg(test)]
mod tests;
