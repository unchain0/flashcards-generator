use super::{
    ApiError, CompanionState, authorized_user,
    jobs::JobRead,
    notebooklm_access::{self, NotebookLMAccess},
};
use axum::{
    Json,
    extract::{
        FromRequest, Multipart, Request, State,
        multipart::{Field, MultipartError},
    },
    http::StatusCode,
};
use flashcards_domain::identity::UserId;
use flashcards_integrations::{
    job_uploads::{LocalUploads, UploadError},
    local_job_store::StoreError,
};
use flashcards_services::{
    generation_options::GenerationOptions, local_job_registry::RegistryError,
    local_jobs::JobSnapshot,
};
use std::{sync::Arc, time::Duration};

pub(super) const MAX_REQUEST_BYTES: usize = 1025 * 1024 * 1024;
const MAX_TEXT_BYTES: usize = 40_000;
const INVALID_FORM: &str = "O formulário de geração é inválido.";

pub(super) async fn create_job(
    State(state): State<Arc<CompanionState>>,
    request: Request,
) -> Result<(StatusCode, Json<JobRead>), ApiError> {
    let headers = request.headers().clone();
    let owner = authorized_user(&headers, &state.verifier)
        .await
        .map_err(ApiError::Authorization)?;
    let _permit = state.uploads.try_acquire().map_err(|_| {
        ApiError::InvalidUpload(
            StatusCode::TOO_MANY_REQUESTS,
            "Aguarde um envio local terminar antes de enviar outro.",
        )
    })?;
    match notebooklm_access::check(&state.profiles, &owner).await {
        NotebookLMAccess::Authenticated => {}
        NotebookLMAccess::LoginRequired => {
            return Err(ApiError::InvalidUpload(
                StatusCode::CONFLICT,
                "Conecte sua conta do NotebookLM antes de gerar flashcards.",
            ));
        }
        NotebookLMAccess::Unavailable => {
            return Err(ApiError::InvalidUpload(
                StatusCode::SERVICE_UNAVAILABLE,
                "Não foi possível verificar sua sessão do NotebookLM. Tente novamente.",
            ));
        }
    }
    let snapshot = receive_owned_upload(&state, owner, request).await?;
    Ok((StatusCode::ACCEPTED, Json(snapshot.into())))
}

async fn receive_owned_upload(
    state: &CompanionState,
    owner: UserId,
    request: Request,
) -> Result<JobSnapshot, ApiError> {
    let headers = request.headers().clone();
    tokio::time::timeout(Duration::from_secs(600), async {
        let (uploads, options) = receive_upload(request).await?;
        let current = authorized_user(&headers, &state.verifier)
            .await
            .map_err(ApiError::Authorization)?;
        if current != owner {
            return Err(ApiError::InvalidUpload(
                StatusCode::UNAUTHORIZED,
                "Acesso local inválido.",
            ));
        }
        uploads
            .submit(&state.jobs, owner, options)
            .await
            .map_err(ApiError::Upload)
    })
    .await
    .map_err(|_| {
        ApiError::InvalidUpload(
            StatusCode::REQUEST_TIMEOUT,
            "O envio dos arquivos demorou demais. Tente novamente.",
        )
    })?
}

async fn receive_upload(request: Request) -> Result<(LocalUploads, GenerationOptions), ApiError> {
    let mut multipart = Multipart::from_request(request, &())
        .await
        .map_err(|_| ApiError::InvalidUpload(StatusCode::BAD_REQUEST, INVALID_FORM))?;
    let mut uploads = LocalUploads::default();
    let mut fields = Vec::new();
    let mut options = GenerationOptions::default();
    let mut parts = 0;
    while let Some(mut field) = multipart
        .next_field()
        .await
        .map_err(|error| multipart_error(&error))?
    {
        parts += 1;
        if parts > 16 {
            return Err(ApiError::InvalidUpload(
                StatusCode::BAD_REQUEST,
                INVALID_FORM,
            ));
        }
        let name = field
            .name()
            .ok_or(ApiError::InvalidUpload(
                StatusCode::BAD_REQUEST,
                INVALID_FORM,
            ))?
            .to_owned();
        if name == "files" {
            receive_file(&mut uploads, &mut field).await?;
        } else {
            fields.push((name, receive_option(&mut field).await?));
            options = parse_options(&fields)?;
        }
    }
    Ok((uploads, options))
}

fn parse_options(fields: &[(String, String)]) -> Result<GenerationOptions, ApiError> {
    GenerationOptions::from_form(fields).map_err(|_| {
        ApiError::InvalidUpload(
            StatusCode::BAD_REQUEST,
            "As configurações de geração são inválidas.",
        )
    })
}

async fn receive_file(uploads: &mut LocalUploads, field: &mut Field<'_>) -> Result<(), ApiError> {
    let filename =
        field
            .file_name()
            .filter(|name| name.len() <= 4096)
            .ok_or(ApiError::InvalidUpload(
                StatusCode::BAD_REQUEST,
                "O envio de arquivos é inválido.",
            ))?;
    uploads.start_file(filename).map_err(ApiError::Upload)?;
    while let Some(chunk) = field
        .chunk()
        .await
        .map_err(|error| multipart_error(&error))?
    {
        uploads
            .write_chunk(&chunk)
            .await
            .map_err(ApiError::Upload)?;
    }
    uploads.finish_file().map_err(ApiError::Upload)
}

async fn receive_option(field: &mut Field<'_>) -> Result<String, ApiError> {
    if field.file_name().is_some() {
        return Err(ApiError::InvalidUpload(
            StatusCode::BAD_REQUEST,
            INVALID_FORM,
        ));
    }
    read_text(field).await
}

async fn read_text(field: &mut Field<'_>) -> Result<String, ApiError> {
    let mut value = Vec::new();
    while let Some(chunk) = field
        .chunk()
        .await
        .map_err(|error| multipart_error(&error))?
    {
        if chunk.len() > MAX_TEXT_BYTES - value.len() {
            return Err(ApiError::InvalidUpload(
                StatusCode::BAD_REQUEST,
                INVALID_FORM,
            ));
        }
        value.extend_from_slice(&chunk);
    }
    String::from_utf8(value)
        .map_err(|_| ApiError::InvalidUpload(StatusCode::BAD_REQUEST, INVALID_FORM))
}

fn multipart_error(error: &MultipartError) -> ApiError {
    ApiError::InvalidUpload(
        if error.status() == StatusCode::PAYLOAD_TOO_LARGE {
            StatusCode::PAYLOAD_TOO_LARGE
        } else {
            StatusCode::BAD_REQUEST
        },
        "O envio de arquivos é inválido.",
    )
}

pub(super) fn upload_error(error: &UploadError) -> (StatusCode, String) {
    match error {
        UploadError::Input(_) | UploadError::Empty => (StatusCode::BAD_REQUEST, error.to_string()),
        UploadError::TooLarge => (StatusCode::PAYLOAD_TOO_LARGE, error.to_string()),
        UploadError::Store(StoreError::Registry(RegistryError::Full)) => (
            StatusCode::TOO_MANY_REQUESTS,
            "Aguarde uma geração local terminar antes de enviar outra.".into(),
        ),
        UploadError::Store(StoreError::Registry(RegistryError::Closed)) => (
            StatusCode::SERVICE_UNAVAILABLE,
            "O auxiliar local está encerrando. Tente novamente.".into(),
        ),
        _ => {
            flashcards_integrations::monitoring::report_error(error);
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                "Falha local ao receber arquivos.".into(),
            )
        }
    }
}

#[cfg(test)]
mod tests;
