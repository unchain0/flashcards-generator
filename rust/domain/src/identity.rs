use std::{error::Error, fmt};

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct UserId(String);

#[derive(Debug, PartialEq, Eq)]
pub struct InvalidUserId;

impl fmt::Display for InvalidUserId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Invalid local profile identity")
    }
}

impl Error for InvalidUserId {}

impl TryFrom<String> for UserId {
    type Error = InvalidUserId;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        if value.len() != 32
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(InvalidUserId);
        }
        Ok(Self(value))
    }
}

impl UserId {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[cfg(test)]
mod tests;
