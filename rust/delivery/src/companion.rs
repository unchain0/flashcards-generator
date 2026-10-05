use crate::companion_auth::{AuthorizationError, authorized_user};
use axum::{
    Json, Router,
    extract::Request,
    extract::State,
    http::{HeaderMap, HeaderValue, Method, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use flashcards_integrations::{
    companion_auth::RemoteTokenVerifier,
    local_job_store::{LocalJobStore, StoreError},
    notebooklm_browser::NotebookLMBrowserLogin,
    notebooklm_profiles::LocalNotebookLMProfiles,
};
use serde::Serialize;
use std::sync::Arc;
use tower_http::cors::CorsLayer;

mod jobs;
mod notebooklm_access;
mod uploads;

pub struct CompanionState {
    verifier: RemoteTokenVerifier,
    profiles: LocalNotebookLMProfiles,
    browser: NotebookLMBrowserLogin,
    jobs: LocalJobStore,
    uploads: tokio::sync::Semaphore,
}

impl CompanionState {
    #[must_use]
    pub fn new(
        verifier: RemoteTokenVerifier,
        profiles: LocalNotebookLMProfiles,
        browser: NotebookLMBrowserLogin,
        jobs: LocalJobStore,
    ) -> Self {
        Self {
            verifier,
            profiles,
            browser,
            jobs,
            uploads: tokio::sync::Semaphore::new(3),
        }
    }
}

#[derive(Serialize)]
struct NotebookLMStatusRead {
    authenticated: bool,
    status: &'static str,
    message: &'static str,
}

enum ApiError {
    Authorization(AuthorizationError),
    Job(StoreError),
    NotFound(&'static str),
    Upload(flashcards_integrations::job_uploads::UploadError),
    InvalidUpload(StatusCode, &'static str),
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, detail) = match &self {
            Self::Authorization(error) => (
                if matches!(error, AuthorizationError::Unavailable(_)) {
                    StatusCode::SERVICE_UNAVAILABLE
                } else {
                    StatusCode::UNAUTHORIZED
                },
                error.to_string(),
            ),
            Self::NotFound(detail) => (StatusCode::NOT_FOUND, (*detail).into()),
            Self::InvalidUpload(status, detail) => (*status, (*detail).into()),
            Self::Upload(error) => uploads::upload_error(error),
            Self::Job(error) => {
                flashcards_integrations::monitoring::report_error(error);
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Falha local ao acessar geração.".into(),
                )
            }
        };
        (status, Json(serde_json::json!({"detail":detail}))).into_response()
    }
}

/// # Errors
/// Rejects a web origin that cannot be represented in CORS response headers.
pub fn router(state: CompanionState) -> Result<Router, axum::http::header::InvalidHeaderValue> {
    let origin = HeaderValue::from_str(state.verifier.origin())?;
    let cors = CorsLayer::new()
        .allow_origin(vec![origin])
        .allow_methods([Method::GET, Method::POST])
        .allow_headers([header::AUTHORIZATION, header::CONTENT_TYPE]);
    Ok(Router::new()
        .route("/v1/health", get(health))
        .route("/v1/notebooklm/status", get(notebooklm_status))
        .route("/v1/notebooklm/login", post(notebooklm_login))
        .route(
            "/v1/jobs",
            post(uploads::create_job).layer(axum::extract::DefaultBodyLimit::max(
                uploads::MAX_REQUEST_BYTES,
            )),
        )
        .route("/v1/jobs/{job_id}", get(jobs::get_job))
        .route(
            "/v1/jobs/{job_id}/artifacts/{*name}",
            get(jobs::download_artifact),
        )
        .layer(cors)
        .layer(middleware::from_fn(no_store))
        .with_state(Arc::new(state)))
}

async fn health(
    State(state): State<Arc<CompanionState>>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, ApiError> {
    let mut origins = headers.get_all(header::ORIGIN).iter();
    if origins.next().and_then(|value| value.to_str().ok()) != Some(state.verifier.origin())
        || origins.next().is_some()
    {
        return Err(ApiError::Authorization(AuthorizationError::Origin));
    }
    Ok(Json(serde_json::json!({"status":"ok"})))
}

async fn notebooklm_status(
    State(state): State<Arc<CompanionState>>,
    headers: HeaderMap,
) -> Result<Json<NotebookLMStatusRead>, ApiError> {
    let user = authorized_user(&headers, &state.verifier)
        .await
        .map_err(ApiError::Authorization)?;
    let access = notebooklm_access::check(&state.profiles, &user).await;
    let (authenticated, status, message) = match access {
        notebooklm_access::NotebookLMAccess::Authenticated => {
            (true, "authenticated", "authenticated")
        }
        notebooklm_access::NotebookLMAccess::LoginRequired => {
            (false, "login_required", "login required")
        }
        notebooklm_access::NotebookLMAccess::Unavailable => {
            (false, "provider_error", "unable to check authentication")
        }
    };
    Ok(Json(NotebookLMStatusRead {
        authenticated,
        status,
        message,
    }))
}

async fn no_store(request: Request, next: Next) -> Response {
    let mut response = next.run(request).await;
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    response
}

async fn notebooklm_login(
    State(state): State<Arc<CompanionState>>,
    headers: HeaderMap,
) -> Result<Json<NotebookLMStatusRead>, ApiError> {
    let user = authorized_user(&headers, &state.verifier)
        .await
        .map_err(ApiError::Authorization)?;
    let (authenticated, status, message) = match state.browser.login(&state.profiles, &user).await {
        Ok(()) => (true, "authenticated", "authenticated"),
        Err(flashcards_integrations::notebooklm_browser::BrowserLoginError::Unavailable) => (
            false,
            "login_required",
            "Chrome or Chromium is required for NotebookLM login",
        ),
        Err(flashcards_integrations::notebooklm_browser::BrowserLoginError::Timeout) => {
            (false, "login_required", "login timed out")
        }
        Err(flashcards_integrations::notebooklm_browser::BrowserLoginError::Closed) => {
            (false, "login_required", "login cancelled")
        }
        Err(_) => (false, "provider_error", "unable to complete login"),
    };
    Ok(Json(NotebookLMStatusRead {
        authenticated,
        status,
        message,
    }))
}
