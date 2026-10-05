use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, error::Error, fmt};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Difficulty {
    Easy,
    #[default]
    Medium,
    Hard,
}

impl Difficulty {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Easy => "easy",
            Self::Medium => "medium",
            Self::Hard => "hard",
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Quantity {
    Fewer,
    #[default]
    Standard,
    More,
}

impl Quantity {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Fewer => "fewer",
            Self::Standard => "standard",
            Self::More => "more",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "RawOptions")]
pub struct GenerationOptions {
    language: String,
    difficulty: Difficulty,
    quantity: Quantity,
    timeout: u32,
    instructions: String,
    single_cloze: bool,
}

#[derive(Deserialize)]
#[serde(default, deny_unknown_fields)]
struct RawOptions {
    language: String,
    difficulty: Difficulty,
    quantity: Quantity,
    timeout: u32,
    instructions: String,
    single_cloze: bool,
}

impl Default for RawOptions {
    fn default() -> Self {
        Self {
            language: "pt_BR".into(),
            difficulty: Difficulty::Medium,
            quantity: Quantity::Standard,
            timeout: 900,
            instructions: String::new(),
            single_cloze: false,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidGenerationOptions(&'static str);

impl fmt::Display for InvalidGenerationOptions {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "As configurações de geração são inválidas: {}",
            self.0
        )
    }
}

impl Error for InvalidGenerationOptions {}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum FormField {
    Difficulty,
    Instructions,
    Language,
    Quantity,
    SingleCloze,
    Timeout,
}

impl TryFrom<&str> for FormField {
    type Error = InvalidGenerationOptions;

    fn try_from(key: &str) -> Result<Self, Self::Error> {
        match key {
            "difficulty" => Ok(Self::Difficulty),
            "instructions" => Ok(Self::Instructions),
            "language" => Ok(Self::Language),
            "quantity" => Ok(Self::Quantity),
            "single_cloze" => Ok(Self::SingleCloze),
            "timeout" => Ok(Self::Timeout),
            _ => Err(InvalidGenerationOptions("unknown or repeated field")),
        }
    }
}

impl TryFrom<RawOptions> for GenerationOptions {
    type Error = InvalidGenerationOptions;

    fn try_from(raw: RawOptions) -> Result<Self, Self::Error> {
        if !(2..=32).contains(&raw.language.len())
            || !raw
                .language
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
        {
            return Err(InvalidGenerationOptions("language"));
        }
        if !(30..=7200).contains(&raw.timeout) {
            return Err(InvalidGenerationOptions("timeout"));
        }
        if raw.instructions.chars().count() > 10_000 {
            return Err(InvalidGenerationOptions("instructions"));
        }
        Ok(raw.into_options())
    }
}

impl RawOptions {
    fn into_options(self) -> GenerationOptions {
        GenerationOptions {
            language: self.language,
            difficulty: self.difficulty,
            quantity: self.quantity,
            timeout: self.timeout,
            instructions: self.instructions,
            single_cloze: self.single_cloze,
        }
    }

    fn set_field(&mut self, key: FormField, value: &str) -> Result<(), InvalidGenerationOptions> {
        match key {
            FormField::Language => self.language = value.into(),
            FormField::Instructions => self.instructions = value.into(),
            FormField::Difficulty => self.difficulty = parse_difficulty(value)?,
            FormField::Quantity => self.quantity = parse_quantity(value)?,
            FormField::Timeout => {
                self.timeout = value
                    .parse()
                    .map_err(|_| InvalidGenerationOptions("timeout"))?;
            }
            FormField::SingleCloze => {
                self.single_cloze = value
                    .parse()
                    .map_err(|_| InvalidGenerationOptions("single_cloze"))?;
            }
        }
        Ok(())
    }
}

impl Default for GenerationOptions {
    fn default() -> Self {
        RawOptions::default().into_options()
    }
}

impl GenerationOptions {
    #[must_use]
    pub fn language(&self) -> &str {
        &self.language
    }
    #[must_use]
    pub fn difficulty(&self) -> Difficulty {
        self.difficulty
    }
    #[must_use]
    pub fn quantity(&self) -> Quantity {
        self.quantity
    }
    #[must_use]
    pub fn timeout(&self) -> u32 {
        self.timeout
    }
    #[must_use]
    pub fn instructions(&self) -> &str {
        &self.instructions
    }
    #[must_use]
    pub fn single_cloze(&self) -> bool {
        self.single_cloze
    }

    /// # Errors
    /// Rejects unknown or duplicate fields, invalid field values, and exceeded limits.
    pub fn from_form(fields: &[(String, String)]) -> Result<Self, InvalidGenerationOptions> {
        let mut raw = RawOptions::default();
        for (key, value) in unique_fields(fields)? {
            raw.set_field(key, value)?;
        }
        raw.try_into()
    }
}

fn unique_fields(
    fields: &[(String, String)],
) -> Result<BTreeMap<FormField, &str>, InvalidGenerationOptions> {
    let mut values = BTreeMap::new();
    for (key, value) in fields {
        let field = FormField::try_from(key.as_str())?;
        if values.insert(field, value.as_str()).is_some() {
            return Err(InvalidGenerationOptions("unknown or repeated field"));
        }
    }
    Ok(values)
}

fn parse_difficulty(value: &str) -> Result<Difficulty, InvalidGenerationOptions> {
    match value {
        "easy" => Ok(Difficulty::Easy),
        "medium" => Ok(Difficulty::Medium),
        "hard" => Ok(Difficulty::Hard),
        _ => Err(InvalidGenerationOptions("difficulty")),
    }
}

fn parse_quantity(value: &str) -> Result<Quantity, InvalidGenerationOptions> {
    match value {
        "fewer" => Ok(Quantity::Fewer),
        "standard" => Ok(Quantity::Standard),
        "more" => Ok(Quantity::More),
        _ => Err(InvalidGenerationOptions("quantity")),
    }
}

#[cfg(test)]
mod tests;
