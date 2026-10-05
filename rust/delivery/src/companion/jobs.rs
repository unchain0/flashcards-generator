use super::{ApiError, CompanionState, authorized_user};
use axum::{
    Json,
    body::Body,
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::Response,
};
use flashcards_integrations::{job_workspace::WorkspaceError, local_job_store::StoreError};
use flashcards_services::local_jobs::{JobSnapshot, JobStatus};
use serde::Serialize;
use std::sync::Arc;
use tokio::io::AsyncReadExt;
use tokio_util::io::ReaderStream;

#[derive(Serialize)]
pub(super) struct JobRead {
    id: String,
    status: JobStatus,
    message: String,
    filenames: Vec<String>,
    discovered_sources: usize,
    completed_sources: usize,
    skipped_sources: usize,
    failed_sources: usize,
    artifacts: Vec<ArtifactRead>,
    error: Option<String>,
}
#[derive(Serialize)]
struct ArtifactRead {
    name: String,
    url: String,
}

impl From<JobSnapshot> for JobRead {
    fn from(snapshot: JobSnapshot) -> Self {
        let artifacts = snapshot
            .artifacts
            .into_iter()
            .map(|name| ArtifactRead {
                name: name.rsplit('/').next().unwrap_or(&name).to_owned(),
                url: format!("/v1/jobs/{}/artifacts/{}", snapshot.id, encode_path(&name)),
            })
            .collect();
        Self {
            id: snapshot.id,
            status: snapshot.status,
            message: snapshot.message,
            filenames: snapshot.filenames,
            discovered_sources: snapshot.discovered_sources,
            completed_sources: snapshot.completed_sources,
            skipped_sources: snapshot.skipped_sources,
            failed_sources: snapshot.failed_sources,
            artifacts,
            error: snapshot.error,
        }
    }
}

pub(super) async fn get_job(
    State(state): State<Arc<CompanionState>>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<JobRead>, ApiError> {
    let user = authorized_user(&headers, &state.verifier)
        .await
        .map_err(ApiError::Authorization)?;
    let snapshot = state
        .jobs
        .snapshot(&user, &id)
        .map_err(ApiError::Job)?
        .ok_or(ApiError::NotFound("Geração não encontrada"))?;
    Ok(Json(snapshot.into()))
}

pub(super) async fn download_artifact(
    State(state): State<Arc<CompanionState>>,
    Path((id, name)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let user = authorized_user(&headers, &state.verifier)
        .await
        .map_err(ApiError::Authorization)?;
    let file = state
        .jobs
        .artifact(&user, &id, &name)
        .map_err(ApiError::Job)?
        .ok_or(ApiError::NotFound("Arquivo não encontrado"))?;
    let length = file
        .metadata()
        .map_err(|error| ApiError::Job(StoreError::Workspace(WorkspaceError::Io(error))))?
        .len();
    let filename = name.rsplit('/').next().unwrap_or(&name);
    let disposition = format!(
        "attachment; filename=\"flashcards.csv\"; filename*=UTF-8''{}",
        encode_path(filename)
    );
    let mut response = Response::new(Body::from_stream(ReaderStream::new(
        tokio::fs::File::from_std(file).take(length),
    )));
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/csv; charset=utf-8"),
    );
    response
        .headers_mut()
        .insert(header::CONTENT_LENGTH, HeaderValue::from(length));
    response.headers_mut().insert(
        header::CONTENT_DISPOSITION,
        HeaderValue::from_str(&disposition).map_err(|_| {
            ApiError::InvalidUpload(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Unable to encode download filename",
            )
        })?,
    );
    Ok(response)
}

fn encode_path(path: &str) -> String {
    path.bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || b"-._~/".contains(&byte) {
                char::from(byte).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}
