//! FFmpeg capture supervisor
//!
//! Spawns FFmpeg with kmsgrab → VAAPI H.264 → RTP output to localhost.
//! Monitors the process and auto-restarts when Gamescope changes the
//! DRM framebuffer format (which kills kmsgrab).
//!
//! Future v2: Replace FFmpeg with direct DRM/KMS + VAAPI via libdrm/libva FFI.

use std::process::Stdio;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::net::UdpSocket;
use tokio::process::{Child, Command};
use tokio::sync::broadcast;
use tokio::task::JoinHandle;

use crate::stats::PipelineStats;

const RTP_RECV_PORT: u16 = 5004;
const MAX_RESTARTS: u32 = 500;

#[derive(Debug, Clone)]
pub struct QualityPreset {
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bitrate_kbps: u32,
}

impl QualityPreset {
    pub fn from_name(name: &str) -> Self {
        match name {
            "480p30" => Self { width: 854, height: 480, fps: 30, bitrate_kbps: 2500 },
            "720p30" => Self { width: 1280, height: 720, fps: 30, bitrate_kbps: 5000 },
            "720p60" => Self { width: 1280, height: 720, fps: 60, bitrate_kbps: 8000 },
            "1080p30" => Self { width: 1920, height: 1080, fps: 30, bitrate_kbps: 8000 },
            "1080p60" => Self { width: 1920, height: 1080, fps: 60, bitrate_kbps: 12000 },
            _ => Self { width: 1280, height: 720, fps: 30, bitrate_kbps: 5000 },
        }
    }
}

/// Build the FFmpeg command that outputs RTP to localhost UDP.
fn build_ffmpeg_cmd(preset: &QualityPreset, audio: bool) -> Vec<String> {
    let mut args: Vec<String> = vec![
        "ffmpeg".into(),
        "-y".into(),
        "-fflags".into(), "nobuffer".into(),
        "-flags".into(), "low_delay".into(),
        // Video input: KMS/DRM
        "-device".into(), "/dev/dri/card0".into(),
        "-framerate".into(), preset.fps.to_string(),
        "-f".into(), "kmsgrab".into(),
        "-i".into(), "-".into(),
    ];

    // Video encode: VAAPI H.264, minimal latency
    let vf = format!(
        "hwmap=derive_device=vaapi,scale_vaapi=w={}:h={}:format=nv12",
        preset.width, preset.height
    );
    args.extend([
        "-vaapi_device".into(), "/dev/dri/renderD128".into(),
        "-vf".into(), vf,
        "-c:v".into(), "h264_vaapi".into(),
        "-b:v".into(), format!("{}k", preset.bitrate_kbps),
        "-maxrate".into(), format!("{}k", preset.bitrate_kbps),
        "-bufsize".into(), format!("{}k", preset.bitrate_kbps / 2),
        "-g".into(), preset.fps.to_string(),
        "-bf".into(), "0".into(),
    ]);

    // Output: raw H.264 RTP to localhost (prysm-engine reads and relays to WebRTC)
    // Audio will be added in v2 as a separate WebRTC track
    args.extend([
        "-f".into(), "rtp".into(),
        "-flush_packets".into(), "1".into(),
        "-sdp_file".into(), "/tmp/prysm/stream.sdp".into(),
        format!("rtp://127.0.0.1:{RTP_RECV_PORT}"),
    ]);

    args
}

/// Spawn FFmpeg process.
fn spawn_ffmpeg(preset: &QualityPreset, audio: bool) -> anyhow::Result<Child> {
    let args = build_ffmpeg_cmd(preset, audio);
    tracing::info!("FFmpeg cmd: {}", args.join(" "));

    let child = Command::new(&args[0])
        .args(&args[1..])
        .env("XDG_RUNTIME_DIR", "/run/user/1000")
        .env("PULSE_SERVER", "/run/user/1000/pulse/native")
        .env("HOME", "/home/deck")
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()?;

    tracing::info!("FFmpeg started (PID {})", child.id().unwrap_or(0));
    Ok(child)
}

/// Start the capture supervisor + RTP receiver.
///
/// Returns a broadcast receiver for RTP packets and a join handle for the supervisor task.
pub async fn start_capture_supervisor(
    preset: QualityPreset,
    audio: bool,
    stats: Arc<PipelineStats>,
) -> anyhow::Result<(broadcast::Receiver<Vec<u8>>, JoinHandle<()>)> {
    // Broadcast channel: FFmpeg → RTP packets → all WebRTC peers
    // Large buffer to handle keyframe bursts (IDR can be 50+ packets)
    let (tx, rx) = broadcast::channel::<Vec<u8>>(2048);

    // UDP socket to receive RTP from FFmpeg
    let sock = UdpSocket::bind(format!("127.0.0.1:{RTP_RECV_PORT}")).await?;
    tracing::info!("RTP receiver listening on 127.0.0.1:{RTP_RECV_PORT}");

    // Spawn the RTP reader task
    let tx_rtp = tx.clone();
    let rtp_stats = stats.clone();
    tokio::spawn(async move {
        let mut buf = vec![0u8; 65536];
        loop {
            match sock.recv(&mut buf).await {
                Ok(n) if n > 0 => {
                    rtp_stats.rtp_packets_recv.inc();
                    rtp_stats.rtp_bytes_recv.add(n as u64);
                    let _ = tx_rtp.send(buf[..n].to_vec());
                }
                Ok(_) => {}
                Err(e) => {
                    rtp_stats.rtp_recv_errors.inc();
                    tracing::warn!("RTP recv error: {e}");
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
            }
        }
    });

    // Spawn the FFmpeg supervisor task
    let sup_stats = stats.clone();
    let handle = tokio::spawn(async move {
        let mut restart_count: u32 = 0;

        loop {
            let mut child = match spawn_ffmpeg(&preset, audio) {
                Ok(c) => c,
                Err(e) => {
                    tracing::error!("Failed to spawn FFmpeg: {e}");
                    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                    restart_count += 1;
                    if restart_count > MAX_RESTARTS {
                        tracing::error!("Too many FFmpeg restarts, giving up");
                        return;
                    }
                    continue;
                }
            };

            // Monitor stderr for the kmsgrab format change error
            if let Some(stderr) = child.stderr.take() {
                let reader = BufReader::new(stderr);
                let mut lines = reader.lines();

                loop {
                    tokio::select! {
                        // Check if process exited
                        status = child.wait() => {
                            match status {
                                Ok(s) => tracing::warn!("FFmpeg exited: {s}"),
                                Err(e) => tracing::warn!("FFmpeg wait error: {e}"),
                            }
                            break;
                        }
                        // Watch stderr for format change (proactive restart)
                        line = lines.next_line() => {
                            match line {
                                Ok(Some(l)) if l.contains("framebuffer format changed") => {
                                    tracing::warn!("Gamescope format change detected — killing FFmpeg");
                                    sup_stats.ffmpeg_format_changes.inc();
                                    let _ = child.kill().await;
                                    break;
                                }
                                Ok(Some(_)) => {} // normal stderr output
                                Ok(None) => break, // EOF
                                Err(_) => break,
                            }
                        }
                    }
                }
            } else {
                // No stderr handle, just wait for exit
                let _ = child.wait().await;
            }

            restart_count += 1;
            sup_stats.ffmpeg_restarts.inc();
            tracing::info!("Restarting FFmpeg (attempt {restart_count})");
            // Brief pause before restart
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        }
    });

    Ok((rx, handle))
}
