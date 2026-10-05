use crate::{
    document_files::MAX_DOCUMENT_BYTES,
    local_job_store::{JobReservation, LocalJobStore, StoreError},
};
use flashcards_domain::identity::UserId;
use flashcards_services::{
    document_inputs::{InvalidDocumentInput, SourceFilename},
    generation_options::GenerationOptions,
    local_jobs::JobSnapshot,
};
use std::{error::Error, fmt, io};
use tokio::{
    fs::File,
    io::{AsyncSeekExt, AsyncWriteExt},
};

pub const MAX_JOB_UPLOAD_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Debug)]
pub enum UploadError {
    Input(InvalidDocumentInput),
    Empty,
    TooLarge,
    Io(io::Error),
    Store(StoreError),
}

#[cfg(test)]
mod tests;
impl fmt::Display for UploadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Input(error) => error.fmt(f),
            Self::Empty => f.write_str("Não é possível gerar flashcards com um arquivo vazio."),
            Self::TooLarge => f.write_str("Os arquivos excedem o limite de tamanho permitido."),
            Self::Io(_) => f.write_str("Falha local ao receber arquivos."),
            Self::Store(error) => error.fmt(f),
        }
    }
}
impl Error for UploadError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Input(error) => Some(error),
            Self::Io(error) => Some(error),
            Self::Store(error) => Some(error),
            _ => None,
        }
    }
}
impl From<io::Error> for UploadError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Default)]
pub struct LocalUploads {
    files: Vec<(SourceFilename, File, u64)>,
    total_bytes: u64,
}
impl LocalUploads {
    /// # Errors
    /// Rejects unsupported filenames and excessive file counts; propagates temporary-file failures.
    pub fn start_file(&mut self, original: &str) -> Result<(), UploadError> {
        let name = SourceFilename::from_upload(self.files.len() + 1, original)
            .map_err(UploadError::Input)?;
        // Anonymous files disappear on cancellation without exposing original names.
        let file = tempfile::tempfile()?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
        }
        let file = File::from_std(file);
        self.files.push((name, file, 0));
        Ok(())
    }

    /// # Errors
    /// Rejects missing files and exceeded size limits; propagates local write failures.
    pub async fn write_chunk(&mut self, chunk: &[u8]) -> Result<(), UploadError> {
        let (_, file, bytes) = self.files.last_mut().ok_or(UploadError::Empty)?;
        let length = chunk.len() as u64;
        if length > MAX_DOCUMENT_BYTES - *bytes || length > MAX_JOB_UPLOAD_BYTES - self.total_bytes
        {
            return Err(UploadError::TooLarge);
        }
        file.write_all(chunk).await?;
        *bytes += length;
        self.total_bytes += length;
        Ok(())
    }

    /// # Errors
    /// Rejects missing or empty files.
    pub fn finish_file(&self) -> Result<(), UploadError> {
        if self.files.last().is_none_or(|(_, _, bytes)| *bytes == 0) {
            return Err(UploadError::Empty);
        }
        Ok(())
    }

    /// # Errors
    /// Rejects empty sources and propagates reservation, persistence, and submission failures.
    pub async fn submit(
        self,
        jobs: &LocalJobStore,
        owner: UserId,
        options: GenerationOptions,
    ) -> Result<JobSnapshot, UploadError> {
        if self.files.is_empty() {
            return Err(UploadError::Input(InvalidDocumentInput::FileCount));
        }
        if self.files.iter().any(|(_, _, bytes)| *bytes == 0) {
            return Err(UploadError::Empty);
        }
        let names = self.files.iter().map(|(name, _, _)| name.clone()).collect();
        let reservation = jobs
            .reserve(owner, names, options)
            .map_err(UploadError::Store)?;
        for (name, source, length) in self.files {
            persist_upload(&reservation, &name, source, length).await?;
        }
        reservation.submit().map_err(UploadError::Store)
    }
}

async fn persist_upload(
    reservation: &JobReservation,
    name: &SourceFilename,
    mut source: File,
    length: u64,
) -> Result<(), UploadError> {
    source.rewind().await?;
    let mut output = File::from_std(reservation.create_input(name).map_err(UploadError::Store)?);
    let copied = tokio::io::copy(&mut source, &mut output).await?;
    if copied != length {
        return Err(UploadError::Io(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "Local upload changed",
        )));
    }
    output.sync_all().await?;
    Ok(())
}
