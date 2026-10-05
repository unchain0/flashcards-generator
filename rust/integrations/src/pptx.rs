use crate::document_files::{MAX_DOCUMENT_BYTES, open_regular, private_tempdir};
use crate::process::{ProcessError, run_bounded};
use reqwest::Url;
use std::{
    error::Error,
    fmt,
    fs::{self, File},
    io::{self, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::process::Command;

#[derive(Debug)]
pub enum ConversionError {
    InvalidInput,
    InvalidOutput,
    Io(io::Error),
    Process(ProcessError),
    Worker(tokio::task::JoinError),
}
impl fmt::Display for ConversionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidInput => "Invalid local PPTX source",
            Self::InvalidOutput => "LibreOffice did not produce a valid PDF",
            Self::Io(_) => "Local presentation conversion failed",
            Self::Process(_) => "LibreOffice presentation conversion failed",
            Self::Worker(_) => "Local presentation worker failed",
        })
    }
}
impl Error for ConversionError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Process(error) => Some(error),
            Self::Worker(error) => Some(error),
            _ => None,
        }
    }
}
impl From<io::Error> for ConversionError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub struct PptxConverter {
    executable: PathBuf,
}
impl Default for PptxConverter {
    fn default() -> Self {
        Self::new(PathBuf::from("soffice"))
    }
}
impl PptxConverter {
    #[must_use]
    pub fn new(executable: PathBuf) -> Self {
        Self { executable }
    }

    /// # Errors
    /// Rejects unsafe source or output paths and propagates isolated conversion and output validation failures.
    pub async fn convert(&self, source: &Path, output: &Path) -> Result<PathBuf, ConversionError> {
        let source = source.to_owned();
        let output = output.to_owned();
        let requested_output = output.clone();
        let directory = tokio::task::spawn_blocking(move || prepare(&source, &requested_output))
            .await
            .map_err(ConversionError::Worker)??;
        let profile = Url::from_directory_path(directory.path().join("profile"))
            .map_err(|()| ConversionError::InvalidInput)?;
        let result = run_bounded(
            Command::new(&self.executable)
                .arg(format!("-env:UserInstallation={profile}"))
                .args([
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--norestore",
                    "--convert-to",
                    "pdf:impress_pdf_Export",
                    "--outdir",
                ])
                .arg(directory.path())
                .arg(directory.path().join("source.pptx")),
            Duration::from_secs(120),
            65_536,
        )
        .await
        .map_err(ConversionError::Process)?;
        if !result.status.success() {
            return Err(ConversionError::InvalidOutput);
        }
        let converted = directory.path().join("source.pdf");
        let verified = run_bounded(
            Command::new("qpdf").arg("--check").arg(&converted),
            Duration::from_secs(30),
            65_536,
        )
        .await
        .map_err(ConversionError::Process)?;
        if !verified.status.success() {
            return Err(ConversionError::InvalidOutput);
        }
        let result = output.clone();
        tokio::task::spawn_blocking(move || publish(&directory.path().join("source.pdf"), &result))
            .await
            .map_err(ConversionError::Worker)??;
        Ok(output)
    }
}

fn prepare(source: &Path, output: &Path) -> Result<tempfile::TempDir, ConversionError> {
    if !source.is_absolute()
        || !output.is_absolute()
        || source
            .extension()
            .and_then(|value| value.to_str())
            .is_none_or(|value| !value.eq_ignore_ascii_case("pptx"))
        || output
            .extension()
            .and_then(|value| value.to_str())
            .is_none_or(|value| !value.eq_ignore_ascii_case("pdf"))
    {
        return Err(ConversionError::InvalidInput);
    }
    let mut input = private_input(source)?;
    let metadata = input.metadata()?;
    if metadata.len() == 0 || metadata.len() > MAX_DOCUMENT_BYTES {
        return Err(ConversionError::InvalidInput);
    }
    let mut header = [0_u8; 4];
    input
        .read_exact(&mut header)
        .map_err(|_| ConversionError::InvalidInput)?;
    if &header != b"PK\x03\x04" {
        return Err(ConversionError::InvalidInput);
    }
    input.seek(SeekFrom::Start(0))?;
    let parent = output.parent().ok_or(ConversionError::InvalidInput)?;
    let parent_metadata = fs::symlink_metadata(parent)?;
    if !parent_metadata.is_dir() {
        return Err(ConversionError::InvalidInput);
    }
    let directory = private_tempdir(".pptx-", Some(parent))?;
    let mut staged = tempfile::NamedTempFile::new_in(directory.path())?;
    let count = io::copy(&mut input.take(MAX_DOCUMENT_BYTES + 1), &mut staged)?;
    if count != metadata.len() {
        return Err(ConversionError::InvalidInput);
    }
    staged.flush()?;
    staged.as_file().sync_all()?;
    staged
        .persist(directory.path().join("source.pptx"))
        .map_err(|error| error.error)?;
    Ok(directory)
}

fn private_input(path: &Path) -> Result<File, ConversionError> {
    open_regular(path).map_err(|error| {
        if error.kind() == io::ErrorKind::InvalidInput {
            ConversionError::InvalidInput
        } else {
            ConversionError::Io(error)
        }
    })
}

fn publish(source: &Path, output: &Path) -> Result<(), ConversionError> {
    let input = private_input(source)?;
    let size = input.metadata()?.len();
    if size == 0 || size > MAX_DOCUMENT_BYTES {
        return Err(ConversionError::InvalidOutput);
    }
    let parent = output.parent().ok_or(ConversionError::InvalidInput)?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".pdf-")
        .tempfile_in(parent)?;
    let count = io::copy(&mut input.take(MAX_DOCUMENT_BYTES + 1), &mut temporary)?;
    if count != size {
        return Err(ConversionError::InvalidOutput);
    }
    temporary.flush()?;
    temporary.as_file().sync_all()?;
    let mut header = [0_u8; 5];
    File::open(temporary.path())?.read_exact(&mut header)?;
    if &header != b"%PDF-" {
        return Err(ConversionError::InvalidOutput);
    }
    temporary.persist(output).map_err(|error| error.error)?;
    #[cfg(unix)]
    File::open(parent)?.sync_all()?;
    Ok(())
}

#[cfg(test)]
mod tests;
