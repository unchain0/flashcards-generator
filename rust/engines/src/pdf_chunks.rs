use std::{error::Error, fmt, ops::Range};

const SKIP_TITLES: &[&str] = &[
    "copyright",
    "table of contents",
    "toc",
    "preface",
    "acknowledgments",
    "index",
    "bibliography",
    "about the author",
    "about the authors",
    "foreword",
    "dedication",
    "about the reviewer",
    "about the technical reviewer",
    "who this book is for",
    "what this book covers",
    "to get the most out of this book",
    "conventions used",
    "get in touch",
    "share your thoughts",
    "download a free pdf",
    " Errata ",
    " piracy ",
    "questions",
    "why subscribe",
    "other books you may enjoy",
    "packt.com",
    "packtpub.com",
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Chapter {
    pub pages: Range<usize>,
    pub title: String,
}

#[derive(Default, Debug, PartialEq, Eq)]
pub struct PdfChunk {
    pub pages: Vec<Range<usize>>,
    pub titles: Vec<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidChunkPlan;
impl fmt::Display for InvalidChunkPlan {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Invalid PDF page count or chunk configuration")
    }
}
impl Error for InvalidChunkPlan {}

pub struct PdfChunkPlanner {
    size: usize,
    overlap: usize,
}
impl Default for PdfChunkPlanner {
    fn default() -> Self {
        Self {
            size: 30,
            overlap: 5,
        }
    }
}
impl PdfChunkPlanner {
    /// # Errors
    /// Rejects zero or excessive sizes and overlap greater than or equal to size.
    pub fn new(size: usize, overlap: usize) -> Result<Self, InvalidChunkPlan> {
        if size == 0 || size > 10_000 || overlap >= size {
            return Err(InvalidChunkPlan);
        }
        Ok(Self { size, overlap })
    }

    /// # Errors
    /// Rejects page or chapter counts greater than 10,000.
    pub fn plan(
        &self,
        total: usize,
        chapters: &[Chapter],
        chapter_overlap: bool,
    ) -> Result<Vec<PdfChunk>, InvalidChunkPlan> {
        if total > 10_000 || chapters.len() > 10_000 {
            return Err(InvalidChunkPlan);
        }
        if total == 0 {
            return Ok(Vec::new());
        }
        if chapters.is_empty() {
            return Ok(self.fixed_chunks(total));
        }
        let mut plan = ChapterPlan::new(chapters[0].pages.start.min(total));
        for chapter in chapters {
            plan.push(chapter, total, self, chapter_overlap);
        }
        if plan.relevant {
            plan.chunks.push(plan.current);
        }
        Ok(plan.chunks)
    }

    fn fixed_chunks(&self, total: usize) -> Vec<PdfChunk> {
        let stride = self.size - self.overlap;
        let count = total
            .saturating_sub(self.overlap)
            .div_ceil(stride)
            .max(1)
            .min(total);
        (0..count)
            .map(|index| fixed_chunk(index * stride, self.size, total))
            .collect()
    }
}

fn fixed_chunk(start: usize, size: usize, total: usize) -> PdfChunk {
    PdfChunk {
        pages: std::iter::once(start..(start + size).min(total)).collect(),
        titles: Vec::new(),
    }
}

struct ChapterPlan {
    chunks: Vec<PdfChunk>,
    current: PdfChunk,
    relevant: bool,
    count: usize,
    end: usize,
}

impl ChapterPlan {
    fn new(prefix_pages: usize) -> Self {
        let mut current = PdfChunk::default();
        if prefix_pages > 0 {
            current.pages.push(0..prefix_pages);
        }
        Self {
            chunks: Vec::new(),
            current,
            relevant: false,
            count: prefix_pages,
            end: prefix_pages,
        }
    }

    fn push(&mut self, chapter: &Chapter, total: usize, planner: &PdfChunkPlanner, overlap: bool) {
        let start = chapter.pages.start.min(total);
        let stop = chapter.pages.end.max(start).min(total);
        let pages = stop - start;
        if self.count > 0 && self.count + pages > planner.size {
            self.rollover(start, planner.overlap, overlap);
        }
        if start < stop {
            self.current.pages.push(start..stop);
        }
        if !self.current.titles.contains(&chapter.title) {
            self.current.titles.push(chapter.title.clone());
            let lower = chapter.title.to_lowercase();
            self.relevant |= !SKIP_TITLES.iter().any(|pattern| lower.contains(pattern));
        }
        self.count += pages;
        self.end = self.end.max(stop);
    }

    fn rollover(&mut self, start: usize, overlap: usize, retain: bool) {
        let retain_overlap = self.relevant && retain;
        let completed = std::mem::take(&mut self.current);
        if self.relevant {
            self.chunks.push(completed);
        }
        self.count = 0;
        self.relevant = false;
        if !retain_overlap {
            self.end = start;
            return;
        }
        let from = self.end.saturating_sub(overlap);
        if from < self.end {
            self.current.pages.push(from..self.end);
        }
        self.count = self.end - from;
    }
}

#[cfg(test)]
mod tests;
