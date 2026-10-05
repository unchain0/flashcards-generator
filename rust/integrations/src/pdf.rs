use crate::{
    document_files::{MAX_DOCUMENT_BYTES, open_regular, private_tempdir},
    process::{ProcessError, run_bounded},
};
use flashcards_engines::pdf_chunks::{Chapter, PdfChunk, PdfChunkPlanner};
use serde::Deserialize;
use std::{
    error::Error,
    fmt,
    fs::File,
    io::{self, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::process::Command;

#[derive(Debug)]
pub enum PdfError {
    InvalidInput,
    InvalidStructure,
    Io(io::Error),
    Process(ProcessError),
    Worker(tokio::task::JoinError),
    Timeout,
}
impl fmt::Display for PdfError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidInput => "Invalid local PDF source",
            Self::InvalidStructure => "Invalid or unsupported PDF structure",
            Self::Io(_) => "Local PDF processing failed",
            Self::Process(_) => "PDF processor failed",
            Self::Worker(_) => "Local PDF worker failed",
            Self::Timeout => "PDF preparation timed out",
        })
    }
}
impl Error for PdfError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Process(error) => Some(error),
            Self::Worker(error) => Some(error),
            _ => None,
        }
    }
}
impl From<io::Error> for PdfError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub struct PreparedPdf {
    directory: tempfile::TempDir,
    files: Vec<PathBuf>,
    total_pages: usize,
}
impl PreparedPdf {
    #[must_use]
    pub fn files(&self) -> &[PathBuf] {
        &self.files
    }
    #[must_use]
    pub fn total_pages(&self) -> usize {
        self.total_pages
    }
    #[must_use]
    pub fn directory(&self) -> &Path {
        self.directory.path()
    }
}

#[derive(Default)]
pub struct PdfProcessor {
    planner: PdfChunkPlanner,
}
impl PdfProcessor {
    #[must_use]
    pub fn new(planner: PdfChunkPlanner) -> Self {
        Self { planner }
    }

    /// # Errors
    /// Rejects invalid or excessive documents and propagates bounded processor, metadata, and private chunk persistence failures.
    pub async fn prepare(
        &self,
        source: &Path,
        threshold: usize,
        use_chapters: bool,
    ) -> Result<PreparedPdf, PdfError> {
        tokio::time::timeout(
            Duration::from_secs(600),
            self.prepare_inner(source, threshold, use_chapters),
        )
        .await
        .map_err(|_| PdfError::Timeout)?
    }

    async fn prepare_inner(
        &self,
        source: &Path,
        threshold: usize,
        use_chapters: bool,
    ) -> Result<PreparedPdf, PdfError> {
        let source = source.to_owned();
        let directory = tokio::task::spawn_blocking(move || snapshot(&source))
            .await
            .map_err(PdfError::Worker)??;
        let input = directory.path().join("source.pdf");
        let metadata = run_bounded(
            Command::new("qpdf")
                .args(["--json=2", "--json-key=pages", "--json-key=outlines"])
                .arg(&input),
            Duration::from_secs(30),
            16 * 1024 * 1024,
        )
        .await
        .map_err(PdfError::Process)?;
        if !metadata.status.success() && metadata.status.code() != Some(3) {
            return Err(PdfError::InvalidStructure);
        }
        let structure = parse_structure(&metadata.stdout)?;
        let total_pages = structure.pages.len();
        if total_pages <= threshold {
            return Ok(PreparedPdf {
                directory,
                files: vec![input],
                total_pages,
            });
        }
        let chapters = if use_chapters {
            structure.chapters()?
        } else {
            Vec::new()
        };
        let chunks = self
            .planner
            .plan(total_pages, &chapters, false)
            .map_err(|_| PdfError::InvalidStructure)?;
        let mut files = Vec::new();
        let mut output_bytes = 0_u64;
        for (index, chunk) in chunks.into_iter().enumerate() {
            let output = directory
                .path()
                .join(format!("source_chunk_{:03}.pdf", index + 1));
            write_chunk(&input, &output, &chunk, &mut output_bytes).await?;
            files.push(output);
        }
        Ok(PreparedPdf {
            directory,
            files,
            total_pages,
        })
    }
}

async fn write_chunk(
    input: &Path,
    output: &Path,
    chunk: &PdfChunk,
    output_bytes: &mut u64,
) -> Result<(), PdfError> {
    let ranges = chunk
        .pages
        .iter()
        .map(|range| format!("{}-{}", range.start + 1, range.end))
        .collect::<Vec<_>>()
        .join(",");
    File::create(output)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(output, std::fs::Permissions::from_mode(0o600))?;
    }
    let mut command = Command::new("qpdf");
    command.arg("--empty");
    if !ranges.is_empty() {
        command.arg("--pages").arg(input).arg(&ranges).arg("--");
    }
    let result = run_bounded(command.arg(output), Duration::from_secs(30), 65_536)
        .await
        .map_err(PdfError::Process)?;
    if !result.status.success() && result.status.code() != Some(3) {
        return Err(PdfError::InvalidStructure);
    }
    let size = open_regular(output)?.metadata()?.len();
    *output_bytes = output_bytes.saturating_add(size);
    if size == 0 || size > MAX_DOCUMENT_BYTES || *output_bytes > 4 * MAX_DOCUMENT_BYTES {
        return Err(PdfError::InvalidStructure);
    }
    Ok(())
}

fn snapshot(source: &Path) -> Result<tempfile::TempDir, PdfError> {
    if !source.is_absolute()
        || source
            .extension()
            .and_then(|value| value.to_str())
            .is_none_or(|value| !value.eq_ignore_ascii_case("pdf"))
    {
        return Err(PdfError::InvalidInput);
    }
    let mut input = open_regular(source)?;
    let size = input.metadata()?.len();
    if size == 0 || size > MAX_DOCUMENT_BYTES {
        return Err(PdfError::InvalidInput);
    }
    let mut header = [0_u8; 5];
    input
        .read_exact(&mut header)
        .map_err(|_| PdfError::InvalidInput)?;
    if &header != b"%PDF-" {
        return Err(PdfError::InvalidInput);
    }
    input.seek(SeekFrom::Start(0))?;
    let directory = private_tempdir(".flashcards-pdf-", None)?;
    let mut output = tempfile::NamedTempFile::new_in(directory.path())?;
    if io::copy(&mut input.take(MAX_DOCUMENT_BYTES + 1), &mut output)? != size {
        return Err(PdfError::InvalidInput);
    }
    output.flush()?;
    output.as_file().sync_all()?;
    output
        .persist(directory.path().join("source.pdf"))
        .map_err(|error| error.error)?;
    Ok(directory)
}

#[derive(Deserialize)]
struct PdfStructure {
    version: u8,
    pages: Vec<PdfPage>,
    outlines: Vec<Outline>,
}
#[derive(Deserialize)]
struct PdfPage {
    pageposfrom1: usize,
}
#[derive(Deserialize)]
struct Outline {
    destpageposfrom1: Option<usize>,
    title: String,
    kids: Vec<Outline>,
}
fn parse_structure(bytes: &[u8]) -> Result<PdfStructure, PdfError> {
    let value: PdfStructure =
        serde_json::from_slice(bytes).map_err(|_| PdfError::InvalidStructure)?;
    if value.version != 2
        || value.pages.len() > 10_000
        || value
            .pages
            .iter()
            .enumerate()
            .any(|(index, page)| page.pageposfrom1 != index + 1)
    {
        return Err(PdfError::InvalidStructure);
    }
    Ok(value)
}
impl PdfStructure {
    fn chapters(&self) -> Result<Vec<Chapter>, PdfError> {
        let flat = flatten_outlines(&self.outlines)?;
        let total = self.pages.len();
        let page = |outline: &Outline| {
            outline
                .destpageposfrom1
                .filter(|&value| value > 0 && value <= total)
                .map(|value| value - 1)
        };
        Ok(flat
            .iter()
            .enumerate()
            .filter_map(|(index, outline)| {
                let start = page(outline)?;
                let end = flat
                    .get(index + 1)
                    .and_then(|next| page(next))
                    .unwrap_or(total);
                (end > start).then(|| Chapter {
                    pages: start..end,
                    title: outline.title.clone(),
                })
            })
            .collect())
    }
}

fn flatten_outlines(outlines: &[Outline]) -> Result<Vec<&Outline>, PdfError> {
    let mut pending = outlines.iter().rev().collect::<Vec<_>>();
    let mut flat = Vec::new();
    while let Some(outline) = pending.pop() {
        if flat.len() >= 10_000 || outline.title.len() > 65_536 {
            return Err(PdfError::InvalidStructure);
        }
        flat.push(outline);
        pending.extend(outline.kids.iter().rev());
    }
    Ok(flat)
}

#[cfg(test)]
mod tests;
