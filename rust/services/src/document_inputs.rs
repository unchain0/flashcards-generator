use std::{error::Error, fmt};

pub const MAX_JOB_FILES: usize = 10;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceFilename {
    name: String,
    stem_end: usize,
}

impl SourceFilename {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.name
    }

    #[must_use]
    pub fn stem(&self) -> &str {
        &self.name[..self.stem_end]
    }

    /// # Errors
    /// Rejects indices outside the upload limit and unsupported source names.
    pub fn from_upload(index: usize, original: &str) -> Result<Self, InvalidDocumentInput> {
        if !(1..=MAX_JOB_FILES).contains(&index) {
            return Err(InvalidDocumentInput::FileCount);
        }
        safe_filename(index, original)
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum InvalidDocumentInput {
    FileCount,
    FileType,
}

impl fmt::Display for InvalidDocumentInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::FileCount => write!(
                formatter,
                "Envie de 1 a {MAX_JOB_FILES} arquivos PDF ou PPTX."
            ),
            Self::FileType => formatter.write_str("Aceitamos somente arquivos PDF ou PPTX."),
        }
    }
}

impl Error for InvalidDocumentInput {}

/// # Errors
/// Rejects empty or excessive batches and unsupported source names.
pub fn safe_filenames(
    original_names: &[String],
) -> Result<Vec<SourceFilename>, InvalidDocumentInput> {
    if original_names.is_empty() || original_names.len() > MAX_JOB_FILES {
        return Err(InvalidDocumentInput::FileCount);
    }
    original_names
        .iter()
        .enumerate()
        .map(|(index, original)| SourceFilename::from_upload(index + 1, original))
        .collect()
}

fn safe_filename(index: usize, original: &str) -> Result<SourceFilename, InvalidDocumentInput> {
    let normalized = original.replace('\\', "/");
    let name = normalized
        .trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or("");
    let (stem, suffix) = name
        .rsplit_once('.')
        .filter(|(stem, suffix)| !stem.is_empty() && !suffix.is_empty())
        .ok_or(InvalidDocumentInput::FileType)?;
    let suffix = suffix.to_ascii_lowercase();
    if !matches!(suffix.as_str(), "pdf" | "pptx") {
        return Err(InvalidDocumentInput::FileType);
    }
    let mut safe_stem = String::new();
    let mut separating = false;
    for character in stem.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '_' | '-') {
            safe_stem.push(character);
            separating = false;
        } else if !separating {
            safe_stem.push('-');
            separating = true;
        }
    }
    let stem = safe_stem.trim_matches(['-', '_']);
    let stem = if stem.is_empty() {
        "document"
    } else {
        &stem[..stem.len().min(80)]
    };
    let name = format!("{index:02}-{stem}.{suffix}");
    Ok(SourceFilename {
        stem_end: name.len() - suffix.len() - 1,
        name,
    })
}

#[cfg(test)]
mod tests;
