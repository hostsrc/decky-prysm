//! HTTP server — serves WHEP signaling, viewer page, and stats endpoint.

use axum::{
    Router,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{Html, IntoResponse, Json},
    routing::{get, post},
};
use std::sync::Arc;
use tower_http::cors::CorsLayer;

use crate::stats::PipelineStats;
use crate::webrtc_relay::Relay;

struct AppState {
    relay: Relay,
    stats: Arc<PipelineStats>,
    viewer_html: String,
}

/// WHEP endpoint — browser sends SDP offer, we return SDP answer.
async fn whep_handler(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> impl IntoResponse {
    let ct = headers
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    if !ct.contains("application/sdp") {
        state.stats.whep_errors.inc();
        return (StatusCode::BAD_REQUEST, "Content-Type must be application/sdp".to_string());
    }

    match state.relay.handle_whep_offer(body).await {
        Ok(answer_sdp) => (StatusCode::CREATED, answer_sdp),
        Err(e) => {
            state.stats.whep_errors.inc();
            tracing::error!("WHEP error: {e}");
            (StatusCode::INTERNAL_SERVER_ERROR, format!("WHEP error: {e}"))
        }
    }
}

/// Stats endpoint — JSON performance metrics
async fn stats_handler(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let report = state.stats.report().await;
    Json(report)
}

/// Viewer page
async fn viewer_handler(State(state): State<Arc<AppState>>) -> Html<String> {
    Html(state.viewer_html.clone())
}

/// Health check
async fn health_handler() -> &'static str {
    "ok"
}

/// Run the HTTP server.
pub async fn run(port: u16, relay: Relay, stats: Arc<PipelineStats>, viewer_path: &str) -> anyhow::Result<()> {
    let viewer_html = std::fs::read_to_string(viewer_path)
        .unwrap_or_else(|_| include_str!("../../../assets/viewer-webrtc.html").to_string());

    let state = Arc::new(AppState { relay, stats, viewer_html });

    let app = Router::new()
        .route("/", get(viewer_handler))
        .route("/whep", post(whep_handler))
        .route("/stats", get(stats_handler))
        .route("/health", get(health_handler))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr = format!("0.0.0.0:{port}");
    tracing::info!("HTTP server listening on {addr}");
    tracing::info!("  Viewer:  http://0.0.0.0:{port}/");
    tracing::info!("  WHEP:    http://0.0.0.0:{port}/whep");
    tracing::info!("  Stats:   http://0.0.0.0:{port}/stats");

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}
