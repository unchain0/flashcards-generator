use axum::{
    Json, Router,
    extract::{
        DefaultBodyLimit, Multipart, Path as AxumPath, Request, State, rejection::JsonRejection,
    },
    http::{HeaderMap, HeaderValue, Method, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use flashcards_integrations_server::web_database::{DatabaseError, WebDatabase};
use flashcards_services::authentication::WebAuthentication;
use serde::{Deserialize, Serialize};
use std::{path::Path, sync::Arc};
use tower_http::{cors::CorsLayer, services::ServeDir};

pub struct WebState {
    database: WebDatabase,
    authentication: WebAuthentication<WebDatabase>,
    jobs: crate::generate::Jobs,
    production: bool,
    session_ttl_seconds: u32,
}

impl WebState {
    #[must_use]
    pub fn new(database: WebDatabase, production: bool, session_ttl_seconds: u32) -> Self {
        Self {
            authentication: WebAuthentication::new(database.clone(), session_ttl_seconds),
            database,
            jobs: crate::generate::Jobs::default(),
            production,
            session_ttl_seconds,
        }
    }
}

#[derive(Serialize)]
struct AuthRead {
    authenticated: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoginPayload {
    password: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CompanionTokenVerify {
    access_token: String,
}

#[derive(Serialize)]
struct CompanionTokenRead {
    access_token: String,
    expires_in: u64,
}

#[derive(Serialize)]
struct CompanionIdentityRead {
    subject: String,
}

#[derive(Serialize)]
struct HealthRead {
    status: &'static str,
}

#[derive(Serialize)]
struct ErrorRead {
    detail: &'static str,
}

struct ApiError(StatusCode, &'static str);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(ErrorRead { detail: self.1 })).into_response()
    }
}

impl From<DatabaseError> for ApiError {
    fn from(error: DatabaseError) -> Self {
        flashcards_integrations_server::monitoring::report_error(&error);
        Self(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Authentication unavailable",
        )
    }
}

/// # Errors
/// Rejects a missing compiled frontend before accepting hosted requests.
pub fn router(
    state: WebState,
    static_dir: &Path,
    cors_origins: Vec<HeaderValue>,
) -> std::io::Result<Router> {
    if !static_dir.join("index.html").is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "Frontend compilado ausente",
        ));
    }
    let state = Arc::new(state);
    let cors = CorsLayer::new()
        .allow_origin(cors_origins)
        .allow_methods([Method::GET, Method::POST, Method::PUT, Method::OPTIONS])
        .allow_headers([header::CONTENT_TYPE]);
    Ok(Router::new()
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/api/v1/auth/login", post(login))
        .route("/api/v1/auth/logout", post(logout))
        .route("/api/v1/auth/me", get(me))
        .route("/api/v1/auth/companion/token", post(companion_token))
        .route(
            "/api/v1/generate",
            post(generate_document).layer(DefaultBodyLimit::max(
                crate::generate::MAX_UPLOAD_BYTES + 65_536,
            )),
        )
        .route("/api/v1/jobs/{id}", get(generate_status))
        .route("/api/v1/jobs/{id}/csv", get(generate_csv))
        .route(
            "/api/v1/auth/companion/verify",
            post(verify_companion_token),
        )
        .fallback_service(ServeDir::new(static_dir).append_index_html_on_directories(true))
        .layer(DefaultBodyLimit::max(8192))
        .layer(cors)
        .layer(middleware::from_fn_with_state(
            Arc::clone(&state),
            security_headers,
        ))
        .with_state(state))
}

#[derive(Serialize)]
struct JobCreated {
    job_id: String,
}

#[derive(Serialize)]
struct JobStatusRead {
    status: &'static str,
    cards: usize,
    download_url: String,
}

/// Upload a PDF and generate cloze cards on the server. No provider is called
/// in v1: the heuristic generator runs unless an LLM source is wired.
async fn generate_document(
    State(state): State<Arc<WebState>>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Result<(StatusCode, Json<JobCreated>), ApiError> {
    require_auth(&state, &headers).await?;
    let mut upload: Option<Vec<u8>> = None;
    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|_| ApiError(StatusCode::BAD_REQUEST, "Formulário inválido"))?
    {
        if field.name() != Some("file") {
            continue;
        }
        let bytes = field
            .bytes()
            .await
            .map_err(|_| ApiError(StatusCode::BAD_REQUEST, "Arquivo inválido"))?;
        upload = Some(bytes.to_vec());
        break;
    }
    let bytes = upload.ok_or(ApiError(StatusCode::BAD_REQUEST, "Envie um arquivo PDF"))?;
    if bytes.is_empty() {
        return Err(ApiError(StatusCode::BAD_REQUEST, "Arquivo vazio"));
    }
    let text = tokio::task::spawn_blocking(move || {
        crate::generate::TextExtractor::extract(&crate::generate::PdfToText::default(), &bytes)
    })
    .await
    .map_err(|_| ApiError(StatusCode::INTERNAL_SERVER_ERROR, "Falha ao ler o PDF"))?
    .map_err(|_| {
        ApiError(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Não foi possível extrair texto do arquivo",
        )
    })?;
    let cards = crate::generate::source_from_env(crate::generate::llm_configured()).cards(&text);
    if cards.is_empty() {
        return Err(ApiError(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Nenhum cartão pôde ser gerado deste arquivo",
        ));
    }
    let csv = crate::generate::render_csv(&cards);
    let id = state.jobs.insert(csv, cards.len());
    Ok((StatusCode::ACCEPTED, Json(JobCreated { job_id: id })))
}

async fn generate_status(
    State(state): State<Arc<WebState>>,
    headers: HeaderMap,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<JobStatusRead>, ApiError> {
    require_auth(&state, &headers).await?;
    let (_, cards) = state
        .jobs
        .get(&id)
        .ok_or(ApiError(StatusCode::NOT_FOUND, "Geração não encontrada"))?;
    Ok(Json(JobStatusRead {
        status: "done",
        cards,
        download_url: format!("/api/v1/jobs/{id}/csv"),
    }))
}

async fn generate_csv(
    State(state): State<Arc<WebState>>,
    headers: HeaderMap,
    AxumPath(id): AxumPath<String>,
) -> Result<Response, ApiError> {
    require_auth(&state, &headers).await?;
    let (csv, _) = state
        .jobs
        .get(&id)
        .ok_or(ApiError(StatusCode::NOT_FOUND, "Geração não encontrada"))?;
    Ok((
        [
            (header::CONTENT_TYPE, "text/csv; charset=utf-8"),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=\"flashcards.csv\"",
            ),
        ],
        csv,
    )
        .into_response())
}

async fn live() -> Json<HealthRead> {
    Json(HealthRead { status: "ok" })
}

async fn ready(State(state): State<Arc<WebState>>) -> Result<Json<HealthRead>, ApiError> {
    state.database.check().await.map_err(|_| {
        ApiError(
            StatusCode::SERVICE_UNAVAILABLE,
            "Database schema is not ready",
        )
    })?;
    Ok(Json(HealthRead { status: "ok" }))
}

async fn login(
    State(state): State<Arc<WebState>>,
    payload: Result<Json<LoginPayload>, JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(payload) =
        payload.map_err(|_| ApiError(StatusCode::BAD_REQUEST, "Informe a senha"))?;
    let token = state
        .authentication
        .login(&payload.password)
        .await?
        .ok_or(ApiError(StatusCode::UNAUTHORIZED, "Senha inválida"))?;
    auth_response(&state, true, &token, state.session_ttl_seconds)
}

async fn logout(
    State(state): State<Arc<WebState>>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    state.authentication.logout(session_token(&headers)).await?;
    auth_response(&state, false, "", 0)
}

async fn me(
    State(state): State<Arc<WebState>>,
    headers: HeaderMap,
) -> Result<Json<AuthRead>, ApiError> {
    require_auth(&state, &headers).await?;
    Ok(Json(AuthRead {
        authenticated: true,
    }))
}

async fn companion_token(
    State(state): State<Arc<WebState>>,
    headers: HeaderMap,
) -> Result<Json<CompanionTokenRead>, ApiError> {
    let access_token = state
        .authentication
        .create_companion_token(session_token(&headers))
        .await?
        .ok_or(ApiError(
            StatusCode::UNAUTHORIZED,
            "Authentication required",
        ))?;
    Ok(Json(CompanionTokenRead {
        access_token,
        expires_in: 300,
    }))
}

async fn verify_companion_token(
    State(state): State<Arc<WebState>>,
    payload: Result<Json<CompanionTokenVerify>, JsonRejection>,
) -> Result<(StatusCode, Json<CompanionIdentityRead>), ApiError> {
    let Json(payload) =
        payload.map_err(|_| ApiError(StatusCode::BAD_REQUEST, "Invalid companion token"))?;
    if payload.access_token.is_empty() || payload.access_token.len() > 4096 {
        return Err(ApiError(StatusCode::BAD_REQUEST, "Invalid companion token"));
    }
    let subject = state
        .authentication
        .user_for_companion_token(&payload.access_token)
        .await?
        .ok_or(ApiError(
            StatusCode::UNAUTHORIZED,
            "Invalid companion token",
        ))?;
    Ok((StatusCode::CREATED, Json(CompanionIdentityRead { subject })))
}

async fn require_auth(state: &WebState, headers: &HeaderMap) -> Result<(), ApiError> {
    state
        .authentication
        .user_for_session(session_token(headers))
        .await?
        .ok_or(ApiError(
            StatusCode::UNAUTHORIZED,
            "Authentication required",
        ))?;
    Ok(())
}

fn session_token(headers: &HeaderMap) -> Option<&str> {
    let mut tokens = headers
        .get_all(header::COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(';'))
        .filter_map(|cookie| cookie.trim().split_once('='))
        .filter_map(|(name, value)| (name == "flashcards_session").then_some(value));
    let token = tokens.next()?;
    if tokens.next().is_some() {
        return None;
    }
    Some(token)
}

fn auth_response(
    state: &WebState,
    authenticated: bool,
    token: &str,
    max_age: u32,
) -> Result<Response, ApiError> {
    let cookie = format!(
        "flashcards_session={token}; HttpOnly; Max-Age={max_age}; Path=/; SameSite=Lax{}",
        if state.production { "; Secure" } else { "" }
    );
    let mut response = Json(AuthRead { authenticated }).into_response();
    response.headers_mut().insert(
        header::SET_COOKIE,
        HeaderValue::from_str(&cookie).map_err(|_| {
            ApiError(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Unable to issue session cookie",
            )
        })?,
    );
    Ok(response)
}

async fn security_headers(
    State(state): State<Arc<WebState>>,
    request: Request,
    next: Next,
) -> Response {
    let mut response = next.run(request).await;
    let headers = response.headers_mut();
    for (name, value) in [
        (
            "content-security-policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self' http://127.0.0.1:8766 https://o4505598204248064.ingest.us.sentry.io; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        ),
        ("referrer-policy", "no-referrer"),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        (
            "permissions-policy",
            "camera=(), geolocation=(), microphone=()",
        ),
    ] {
        headers.insert(name, HeaderValue::from_static(value));
    }
    if state.production {
        headers.insert(
            "strict-transport-security",
            HeaderValue::from_static("max-age=31536000"),
        );
    }
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

#[cfg(test)]
mod tests;
