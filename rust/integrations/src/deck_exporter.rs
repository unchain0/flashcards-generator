use flashcards_domain::Deck;
use flashcards_engines::math::convert_to_anki_math_format;
use flashcards_services::deck_exporter::DeckExporter;
use std::{fs::File, io, path::Path};

pub struct CsvDeckExporter;

impl DeckExporter for CsvDeckExporter {
    type Error = io::Error;

    fn export_csv(&self, deck: &Deck, path: &Path) -> Result<(), Self::Error> {
        let mut temporary = crate::document_files::private_output_file(path)?;
        write_cards(temporary.as_file_mut(), deck)?;
        temporary.as_file().sync_all()?;
        temporary.persist(path).map_err(|error| error.error)?;
        Ok(())
    }
}

fn write_cards(file: &mut File, deck: &Deck) -> Result<(), io::Error> {
    let mut writer = csv::WriterBuilder::new()
        .quote_style(csv::QuoteStyle::Always)
        .terminator(csv::Terminator::CRLF)
        .from_writer(file);
    for card in &deck.flashcards {
        writer
            .write_record([
                convert_to_anki_math_format(&card.front),
                convert_to_anki_math_format(&card.back),
            ])
            .map_err(io::Error::other)?;
    }
    writer.flush()
}

#[cfg(test)]
mod tests;
