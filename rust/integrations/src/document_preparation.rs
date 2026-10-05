use crate::{
    document_files::private_tempdir,
    pdf::{PdfError, PdfProcessor, PreparedPdf},
    pptx::{ConversionError, PptxConverter},
};
use flashcards_services::document_preparation::{DocumentPreparer, PreparedDocuments};
use std::{
    error::Error,
    fmt, io,
    path::{Path, PathBuf},
};

#[derive(Debug)]
pub enum PreparationError {
    InvalidInput,
    Pdf(PdfError),
    Presentation(ConversionError),
    Io(io::Error),
}
impl fmt::Display for PreparationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Local document preparation failed")
    }
}
impl Error for PreparationError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Pdf(error) => Some(error),
            Self::Presentation(error) => Some(error),
            Self::Io(error) => Some(error),
            Self::InvalidInput => None,
        }
    }
}

#[derive(Default)]
pub struct LocalDocumentPreparer {
    pdf: PdfProcessor,
    presentation: PptxConverter,
}
impl PreparedDocuments for PreparedPdf {
    fn files(&self) -> &[PathBuf] {
        self.files()
    }
}
impl DocumentPreparer for LocalDocumentPreparer {
    type Error = PreparationError;
    type Prepared = PreparedPdf;
    async fn prepare(&self, source: &Path) -> Result<PreparedPdf, PreparationError> {
        match source
            .extension()
            .and_then(|value| value.to_str())
            .map(str::to_ascii_lowercase)
            .as_deref()
        {
            Some("pdf") => self
                .pdf
                .prepare(source, 50, true)
                .await
                .map_err(PreparationError::Pdf),
            Some("pptx") => {
                let directory = private_tempdir(".flashcards-presentation-", None)
                    .map_err(PreparationError::Io)?;
                let output = directory.path().join("source.pdf");
                self.presentation
                    .convert(source, &output)
                    .await
                    .map_err(PreparationError::Presentation)?;
                self.pdf
                    .prepare(&output, 50, true)
                    .await
                    .map_err(PreparationError::Pdf)
            }
            _ => Err(PreparationError::InvalidInput),
        }
    }
}

#[cfg(test)]
mod tests;
