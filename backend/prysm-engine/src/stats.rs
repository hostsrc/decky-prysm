//! Pipeline performance profiler
//!
//! Tracks metrics at every stage of the streaming pipeline:
//!   FFmpeg capture → RTP recv → WebRTC relay → Browser
//!
//! Exposes stats via /stats JSON endpoint and periodic log output.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;
use serde::Serialize;
use tokio::sync::RwLock;

/// Atomic counter helper
pub struct Counter(AtomicU64);
impl Counter {
    pub const fn new() -> Self { Self(AtomicU64::new(0)) }
    pub fn inc(&self) { self.0.fetch_add(1, Ordering::Relaxed); }
    pub fn add(&self, n: u64) { self.0.fetch_add(n, Ordering::Relaxed); }
    pub fn get(&self) -> u64 { self.0.load(Ordering::Relaxed) }
}

/// All pipeline stats — updated atomically from multiple tasks
pub struct PipelineStats {
    start_time: Instant,

    // FFmpeg supervisor
    pub ffmpeg_restarts: Counter,
    pub ffmpeg_format_changes: Counter,

    // RTP receiver (FFmpeg → engine)
    pub rtp_packets_recv: Counter,
    pub rtp_bytes_recv: Counter,
    pub rtp_recv_errors: Counter,

    // Broadcast channel
    pub broadcast_lagged: Counter,

    // WebRTC relay (engine → browser)
    pub webrtc_packets_sent: Counter,
    pub webrtc_bytes_sent: Counter,
    pub webrtc_write_errors: Counter,
    pub webrtc_peers_total: Counter,

    // WHEP signaling
    pub whep_offers: Counter,
    pub whep_errors: Counter,

    // Snapshot for rate calculations
    snapshot: RwLock<StatsSnapshot>,
}

struct StatsSnapshot {
    timestamp: Instant,
    rtp_packets: u64,
    rtp_bytes: u64,
    webrtc_packets: u64,
    webrtc_bytes: u64,
}

/// JSON-serializable stats response
#[derive(Serialize, Clone)]
pub struct StatsReport {
    pub uptime_secs: f64,

    // FFmpeg
    pub ffmpeg_restarts: u64,
    pub ffmpeg_format_changes: u64,

    // RTP ingest (packets per second, megabits per second)
    pub rtp_pps: f64,
    pub rtp_mbps: f64,
    pub rtp_total_packets: u64,
    pub rtp_total_bytes: u64,
    pub rtp_errors: u64,

    // Broadcast
    pub broadcast_lagged: u64,

    // WebRTC egress
    pub webrtc_pps: f64,
    pub webrtc_mbps: f64,
    pub webrtc_total_packets: u64,
    pub webrtc_errors: u64,
    pub webrtc_peers_total: u64,

    // WHEP
    pub whep_offers: u64,
    pub whep_errors: u64,
}

impl PipelineStats {
    pub fn new() -> Arc<Self> {
        let now = Instant::now();
        Arc::new(Self {
            start_time: now,
            ffmpeg_restarts: Counter::new(),
            ffmpeg_format_changes: Counter::new(),
            rtp_packets_recv: Counter::new(),
            rtp_bytes_recv: Counter::new(),
            rtp_recv_errors: Counter::new(),
            broadcast_lagged: Counter::new(),
            webrtc_packets_sent: Counter::new(),
            webrtc_bytes_sent: Counter::new(),
            webrtc_write_errors: Counter::new(),
            webrtc_peers_total: Counter::new(),
            whep_offers: Counter::new(),
            whep_errors: Counter::new(),
            snapshot: RwLock::new(StatsSnapshot {
                timestamp: now,
                rtp_packets: 0,
                rtp_bytes: 0,
                webrtc_packets: 0,
                webrtc_bytes: 0,
            }),
        })
    }

    /// Generate a stats report with rate calculations
    pub async fn report(&self) -> StatsReport {
        let now = Instant::now();
        let uptime = now.duration_since(self.start_time).as_secs_f64();

        let rtp_packets = self.rtp_packets_recv.get();
        let rtp_bytes = self.rtp_bytes_recv.get();
        let webrtc_packets = self.webrtc_packets_sent.get();

        // Calculate rates from snapshot delta
        let mut snap = self.snapshot.write().await;
        let dt = now.duration_since(snap.timestamp).as_secs_f64().max(0.001);
        let rtp_pps = (rtp_packets - snap.rtp_packets) as f64 / dt;
        let rtp_mbps = ((rtp_bytes - snap.rtp_bytes) as f64 * 8.0) / (dt * 1_000_000.0);
        let webrtc_pps = (webrtc_packets - snap.webrtc_packets) as f64 / dt;
        let webrtc_bytes = self.webrtc_bytes_sent.get();
        let webrtc_mbps = ((webrtc_bytes - snap.webrtc_bytes) as f64 * 8.0) / (dt * 1_000_000.0);

        // Update snapshot
        *snap = StatsSnapshot {
            timestamp: now,
            rtp_packets,
            rtp_bytes,
            webrtc_packets,
            webrtc_bytes,
        };

        StatsReport {
            uptime_secs: uptime,
            ffmpeg_restarts: self.ffmpeg_restarts.get(),
            ffmpeg_format_changes: self.ffmpeg_format_changes.get(),
            rtp_pps,
            rtp_mbps,
            rtp_total_packets: rtp_packets,
            rtp_total_bytes: rtp_bytes,
            rtp_errors: self.rtp_recv_errors.get(),
            broadcast_lagged: self.broadcast_lagged.get(),
            webrtc_pps,
            webrtc_mbps,
            webrtc_total_packets: webrtc_packets,
            webrtc_errors: self.webrtc_write_errors.get(),
            webrtc_peers_total: self.webrtc_peers_total.get(),
            whep_offers: self.whep_offers.get(),
            whep_errors: self.whep_errors.get(),
        }
    }
}

/// Spawn a background task that logs stats every N seconds
pub fn spawn_stats_logger(stats: Arc<PipelineStats>, interval_secs: u64) {
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(interval_secs));
        loop {
            interval.tick().await;
            let r = stats.report().await;
            tracing::info!(
                "STATS | up:{:.0}s | rtp:{:.0}pps {:.1}Mbps ({} pkts, {} err) | webrtc:{:.0}pps {:.1}Mbps ({} err) | peers:{} | ffmpeg_restarts:{} | lagged:{} | whep:{}/{}",
                r.uptime_secs,
                r.rtp_pps, r.rtp_mbps, r.rtp_total_packets, r.rtp_errors,
                r.webrtc_pps, r.webrtc_mbps, r.webrtc_errors,
                r.webrtc_peers_total,
                r.ffmpeg_restarts,
                r.broadcast_lagged,
                r.whep_offers, r.whep_errors,
            );
        }
    });
}
