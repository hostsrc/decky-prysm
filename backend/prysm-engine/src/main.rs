//! Prysm Engine — Low-latency screen streaming for Steam Deck
//!
//! Architecture (MVP):
//!   FFmpeg (kmsgrab → VAAPI H.264) → RTP → prysm-engine → WebRTC → Browser
//!
//! Architecture (v2 — native):
//!   PipeWire (Gamescope) → DMA-BUF → VAAPI encode → WebRTC → Browser

mod capture;
mod server;
mod stats;
mod webrtc_relay;

use clap::Parser;
use tracing_subscriber::EnvFilter;

#[derive(Parser, Debug)]
#[command(name = "prysm-engine", about = "Steam Deck screen streaming engine")]
struct Args {
    /// HTTP/WHEP server port
    #[arg(short, long, default_value = "7770")]
    port: u16,

    /// Video quality preset
    #[arg(short, long, default_value = "720p30")]
    quality: String,

    /// Disable audio capture
    #[arg(long, default_value = "false")]
    no_audio: bool,

    /// Path to viewer HTML file
    #[arg(long, default_value = "/tmp/prysm/viewer.html")]
    viewer_path: String,

    /// Stats log interval in seconds (0 to disable)
    #[arg(long, default_value = "5")]
    stats_interval: u64,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .init();

    let args = Args::parse();
    tracing::info!("Prysm Engine starting — port={} quality={}", args.port, args.quality);

    // Pipeline stats — shared across all components
    let stats = stats::PipelineStats::new();

    // Start stats logger
    if args.stats_interval > 0 {
        stats::spawn_stats_logger(stats.clone(), args.stats_interval);
    }

    // Parse quality preset
    let preset = capture::QualityPreset::from_name(&args.quality);
    tracing::info!("Preset: {}x{} @ {}fps, {}kbps", preset.width, preset.height, preset.fps, preset.bitrate_kbps);

    // Start the capture supervisor (spawns FFmpeg, auto-restarts on failure)
    let (rtp_rx, capture_handle) = capture::start_capture_supervisor(preset, !args.no_audio, stats.clone()).await?;

    // Start the WebRTC relay (reads RTP, fans out to WebRTC peers)
    let relay = webrtc_relay::Relay::new(rtp_rx, stats.clone()).await?;

    // Start the HTTP server (WHEP signaling + viewer page + /stats)
    server::run(args.port, relay, stats.clone(), &args.viewer_path).await?;

    // Cleanup
    capture_handle.abort();
    tracing::info!("Prysm Engine shut down");
    Ok(())
}
