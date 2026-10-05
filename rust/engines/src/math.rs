use lazy_regex::{Lazy, lazy_regex};
use regex::Captures;

#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static DISPLAY_DOLLAR: Lazy<regex::Regex> = lazy_regex!(r"\$\$([^$]+)\$\$");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static DISPLAY_BRACKET: Lazy<regex::Regex> = lazy_regex!(r"\\\[(.+?)\\\]");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static INLINE_PAREN: Lazy<regex::Regex> = lazy_regex!(r"\\\((.+?)\\\)");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static INLINE_DOLLAR: Lazy<regex::Regex> = lazy_regex!(r"\$([^$]+)\$");
#[expect(
    clippy::non_std_lazy_statics,
    reason = "Compile-time regex validation uses the library OnceCell initializer"
)]
static SEGMENTS: Lazy<regex::Regex> =
    lazy_regex!(r"\$\$[^$]+\$\$|\$[^$]+\$|\\\[.+?\\\]|\\\(.+?\\\)");

#[derive(Default)]
pub struct MathProcessor {
    storage: Vec<(String, String)>,
}

impl MathProcessor {
    pub fn extract_and_replace(&mut self, text: &str) -> String {
        self.storage.clear();
        let mut result = text.to_owned();
        for pattern in [
            &*DISPLAY_DOLLAR,
            &*DISPLAY_BRACKET,
            &*INLINE_PAREN,
            &*INLINE_DOLLAR,
        ] {
            result = pattern
                .replace_all(&result, |captures: &Captures<'_>| self.store(&captures[0]))
                .into_owned();
        }
        result
    }

    fn store(&mut self, content: &str) -> String {
        let placeholder = format!("MATHPLACEHOLDER{:04}", self.storage.len() + 1);
        self.storage.push((placeholder.clone(), content.to_owned()));
        placeholder
    }

    #[must_use]
    pub fn restore_math(&self, text: &str) -> String {
        let mut entries = self.storage.iter().collect::<Vec<_>>();
        entries.sort_by(|left, right| right.0.cmp(&left.0));
        entries.into_iter().fold(text.to_owned(), |result, entry| {
            result.replace(&entry.0, &entry.1)
        })
    }
}

#[must_use]
pub fn has_math(text: &str) -> bool {
    SEGMENTS.is_match(text)
}

#[must_use]
pub fn extract_math_segments(text: &str) -> Vec<(&str, bool)> {
    let mut segments = Vec::new();
    let mut end = 0;
    for matched in SEGMENTS.find_iter(text) {
        if matched.start() > end {
            segments.push((&text[end..matched.start()], false));
        }
        segments.push((matched.as_str(), true));
        end = matched.end();
    }
    if end < text.len() {
        segments.push((&text[end..], false));
    }
    if segments.is_empty() {
        segments.push((text, false));
    }
    segments
}

#[must_use]
pub fn convert_to_anki_math_format(text: &str) -> String {
    let display = DISPLAY_DOLLAR.replace_all(text, |captures: &Captures<'_>| {
        format!(r"\[{}\]", &captures[1])
    });
    INLINE_DOLLAR
        .replace_all(&display, |captures: &Captures<'_>| {
            format!(r"\({}\)", &captures[1])
        })
        .into_owned()
}

#[must_use]
pub fn create_cloze_with_math(answer: &str, card_num: u32) -> String {
    let cloze = format!("{{{{c{card_num}::{answer}}}}}");
    convert_to_anki_math_format(&cloze)
}

#[cfg(test)]
mod tests;
