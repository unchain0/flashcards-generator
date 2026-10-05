use crate::document_files::{MAX_DOCUMENT_BYTES, open_regular, private_output_file};
use flashcards_services::csv_merge::{CsvMerger, MergeCsvRequest, MergeDetails};
use std::{
    collections::HashSet,
    error::Error,
    fmt, fs,
    io::{self, Read},
    path::{Path, PathBuf},
};

const MAX_ENTRIES: usize = 100_000;
const MAX_ROWS: usize = 1_000_000;

#[derive(Debug)]
pub enum MergeError {
    InvalidFolder,
    NoSources,
    UnsafeSource,
    InvalidColumns,
    Limit,
    Io(io::Error),
    Csv(csv::Error),
}
impl fmt::Display for MergeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidFolder => "CSV merge requires a local directory",
            Self::NoSources => "No CSV sources found",
            Self::UnsafeSource => "CSV merge source is not a regular local file or directory",
            Self::InvalidColumns => "CSV rows must contain exactly two columns",
            Self::Limit => "CSV merge exceeds local processing limits",
            Self::Io(_) | Self::Csv(_) => "Local CSV merge failed",
        })
    }
}
impl Error for MergeError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Csv(error) => Some(error),
            _ => None,
        }
    }
}
impl From<io::Error> for MergeError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}
impl From<csv::Error> for MergeError {
    fn from(error: csv::Error) -> Self {
        Self::Csv(error)
    }
}

pub struct LocalCsvMerger;
impl CsvMerger for LocalCsvMerger {
    type Error = MergeError;
    fn merge(&self, request: &MergeCsvRequest) -> Result<MergeDetails, MergeError> {
        if !fs::symlink_metadata(request.folder())?.is_dir() {
            return Err(MergeError::InvalidFolder);
        }
        let root = fs::canonicalize(request.folder())?;
        let output = root.join(request.filename());
        let files = source_files(&root, &output, request.recursive())?;
        let mut temporary = private_output_file(&output)?;
        let mut details = MergeDetails::default();
        let mut seen = HashSet::new();
        {
            let mut writer = csv::WriterBuilder::new()
                .quote_style(csv::QuoteStyle::Always)
                .terminator(csv::Terminator::CRLF)
                .from_writer(temporary.as_file_mut());
            merge_sources(
                files,
                request.deduplicate(),
                &mut writer,
                &mut details,
                &mut seen,
            )?;
            writer.flush()?;
        }
        temporary.as_file().sync_all()?;
        temporary.persist(output).map_err(|error| error.error)?;
        details.duplicates_removed = details.rows_before - details.rows_written;
        Ok(details)
    }
}

fn merge_sources(
    files: Vec<PathBuf>,
    deduplicate: bool,
    writer: &mut csv::Writer<&mut fs::File>,
    details: &mut MergeDetails,
    seen: &mut HashSet<(String, String)>,
) -> Result<(), MergeError> {
    let mut input_bytes = 0;
    for source in files {
        let bytes = read_source(&source, &mut input_bytes)?;
        // A leading newline keeps the UTF-8 BOM, matching Python's utf-8 reader.
        let input = io::Cursor::new(b"\n").chain(io::Cursor::new(bytes));
        let mut reader = csv::ReaderBuilder::new()
            .has_headers(false)
            .flexible(true)
            .from_reader(input);
        for record in reader.records() {
            merge_record(&record?, deduplicate, writer, details, seen)?;
        }
    }
    Ok(())
}

fn read_source(source: &Path, input_bytes: &mut u64) -> Result<Vec<u8>, MergeError> {
    let file = open_regular(source)?;
    let expected = file.metadata()?.len();
    if expected > MAX_DOCUMENT_BYTES - *input_bytes {
        return Err(MergeError::Limit);
    }
    let mut bytes = Vec::new();
    file.take(MAX_DOCUMENT_BYTES - *input_bytes + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != expected {
        return Err(MergeError::UnsafeSource);
    }
    *input_bytes += expected;
    Ok(bytes)
}

fn merge_record(
    record: &csv::StringRecord,
    deduplicate: bool,
    writer: &mut csv::Writer<&mut fs::File>,
    details: &mut MergeDetails,
    seen: &mut HashSet<(String, String)>,
) -> Result<(), MergeError> {
    if record.len() < 2 {
        return Ok(());
    }
    if record.len() != 2 {
        return Err(MergeError::InvalidColumns);
    }
    if details.rows_before == MAX_ROWS {
        return Err(MergeError::Limit);
    }
    details.rows_before += 1;
    if deduplicate && !seen.insert((strip(&record[0]).to_owned(), strip(&record[1]).to_owned())) {
        return Ok(());
    }
    writer.write_record(record)?;
    details.rows_written += 1;
    Ok(())
}

fn strip(value: &str) -> &str {
    value.trim_matches(|ch: char| ch.is_whitespace() || matches!(ch, '\u{1c}'..='\u{1f}'))
}

fn source_files(root: &Path, output: &Path, recursive: bool) -> Result<Vec<PathBuf>, MergeError> {
    let mut folders = vec![root.to_owned()];
    let mut files = Vec::new();
    let mut visited = 0;
    while let Some(folder) = folders.pop() {
        if !fs::symlink_metadata(&folder)?.is_dir() {
            return Err(MergeError::UnsafeSource);
        }
        collect_folder(
            &folder,
            output,
            recursive,
            &mut folders,
            &mut files,
            &mut visited,
        )?;
    }
    if files.is_empty() {
        return Err(MergeError::NoSources);
    }
    files.sort();
    Ok(files)
}

fn collect_folder(
    folder: &Path,
    output: &Path,
    recursive: bool,
    folders: &mut Vec<PathBuf>,
    files: &mut Vec<PathBuf>,
    visited: &mut usize,
) -> Result<(), MergeError> {
    for entry in fs::read_dir(folder)? {
        *visited += 1;
        if *visited > MAX_ENTRIES {
            return Err(MergeError::Limit);
        }
        let entry = entry?;
        let path = entry.path();
        if path == output {
            continue;
        }
        let kind = entry.file_type()?;
        if recursive && kind.is_dir() {
            folders.push(path);
        } else if path.extension().is_some_and(|extension| extension == "csv") {
            require_regular_source(kind)?;
            files.push(path);
        }
    }
    Ok(())
}

fn require_regular_source(kind: fs::FileType) -> Result<(), MergeError> {
    if !kind.is_file() {
        return Err(MergeError::UnsafeSource);
    }
    Ok(())
}

#[cfg(test)]
mod tests;
