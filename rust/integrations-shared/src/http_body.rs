use reqwest::Response;

pub enum BodyReadError {
    Http(reqwest::Error),
    TooLarge,
}

/// # Errors
/// Rejects oversized bodies and propagates HTTP stream failures.
pub async fn read_bounded(
    mut response: Response,
    maximum: usize,
) -> Result<Vec<u8>, BodyReadError> {
    if response
        .content_length()
        .is_some_and(|size| size > maximum as u64)
    {
        return Err(BodyReadError::TooLarge);
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(BodyReadError::Http)? {
        if chunk.len() > maximum.saturating_sub(bytes.len()) {
            return Err(BodyReadError::TooLarge);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}
