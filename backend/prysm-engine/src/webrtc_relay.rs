//! HTTP MPEG-TS relay — FFmpeg pipes H.264 MPEG-TS, we broadcast to browsers.
//!
//! No WebRTC. No DTLS. No ICE. No STUN. Just raw H.264 bytes over HTTP.
//! Browser uses mpegts.js (MSE) for hardware-accelerated decode.
//! Expected latency on LAN: ~200-500ms.

use std::sync::Arc;
use tokio::sync::broadcast;

use crate::stats::PipelineStats;

/// The relay just holds the broadcast sender for HTTP clients to subscribe to.
pub struct Relay {
    pub tx: broadcast::Sender<Vec<u8>>,
    pub stats: Arc<PipelineStats>,
    _reader_task: tokio::task::JoinHandle<()>,
}

impl Relay {
    pub async fn new(
        mut rtp_rx: broadcast::Receiver<Vec<u8>>,
        stats: Arc<PipelineStats>,
    ) -> anyhow::Result<Self> {
        // HTTP broadcast channel — each browser client gets a receiver
        let (tx, _) = broadcast::channel::<Vec<u8>>(2048);
        let tx_clone = tx.clone();
        let relay_stats = stats.clone();

        // Just pass through UDP packets to HTTP clients
        let reader_task = tokio::spawn(async move {
            let mut count: u64 = 0;
            loop {
                match rtp_rx.recv().await {
                    Ok(packet) => {
                        count += 1;
                        let len = packet.len();
                        relay_stats.rtp_packets_recv.inc();
                        relay_stats.rtp_bytes_recv.add(len as u64);

                        // Broadcast to all HTTP clients
                        if tx_clone.receiver_count() > 0 {
                            let _ = tx_clone.send(packet);
                            relay_stats.webrtc_packets_sent.inc();
                            relay_stats.webrtc_bytes_sent.add(len as u64);
                        }

                        if count <= 3 || count % 5000 == 0 {
                            tracing::info!(
                                "pkt#{count}: {len}B, clients={}",
                                tx_clone.receiver_count()
                            );
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        relay_stats.broadcast_lagged.inc();
                        tracing::warn!("Lagged {n}");
                    }
                    Err(broadcast::error::RecvError::Closed) => break,
                }
            }
        });

        Ok(Self {
            tx,
            stats,
            _reader_task: reader_task,
        })
    }
}
