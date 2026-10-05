use std::{
    error::Error,
    fmt,
    path::{Path, PathBuf},
};

pub const DEFAULT_MERGED_FILENAME: &str = "merged_flashcards.csv";

pub struct MergeCsvRequest {
    folder: PathBuf,
    filename: String,
    recursive: bool,
    deduplicate: bool,
}
#[derive(Debug)]
pub struct InvalidMergeRequest;
impl fmt::Display for InvalidMergeRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Merged CSV filename must be a relative basename")
    }
}
impl Error for InvalidMergeRequest {}
impl MergeCsvRequest {
    /// # Errors
    /// Rejects empty, excessive, unsafe, or non-basename output filenames.
    pub fn new(
        folder: PathBuf,
        filename: String,
        recursive: bool,
        deduplicate: bool,
    ) -> Result<Self, InvalidMergeRequest> {
        if filename.is_empty()
            || filename.len() > 255
            || filename.contains(['/', '\\'])
            || matches!(filename.as_str(), "." | "..")
            || filename.chars().any(char::is_control)
            || Path::new(&filename)
                .file_name()
                .and_then(|name| name.to_str())
                != Some(filename.as_str())
        {
            return Err(InvalidMergeRequest);
        }
        Ok(Self {
            folder,
            filename,
            recursive,
            deduplicate,
        })
    }
    #[must_use]
    pub fn folder(&self) -> &Path {
        &self.folder
    }
    #[must_use]
    pub fn filename(&self) -> &str {
        &self.filename
    }
    #[must_use]
    pub fn recursive(&self) -> bool {
        self.recursive
    }
    #[must_use]
    pub fn deduplicate(&self) -> bool {
        self.deduplicate
    }
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct MergeDetails {
    pub rows_before: usize,
    pub rows_written: usize,
    pub duplicates_removed: usize,
}
pub trait CsvMerger: Send + Sync {
    type Error: Error + Send + Sync + 'static;
    /// # Errors
    /// Propagates source reading, validation, and output persistence errors.
    fn merge(&self, request: &MergeCsvRequest) -> Result<MergeDetails, Self::Error>;
}

#[cfg(test)]
mod tests;
