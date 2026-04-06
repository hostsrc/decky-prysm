//! WebRTC relay — receives RTP packets from FFmpeg and fans them out
//! to all connected WebRTC peers via WHEP (WebRTC-HTTP Egress Protocol).

use std::sync::Arc;
use tokio::sync::{broadcast, Mutex};
use webrtc::api::media_engine::MediaEngine;
use webrtc::api::APIBuilder;

use crate::stats::PipelineStats;
use webrtc::ice_transport::ice_server::RTCIceServer;
use webrtc::peer_connection::configuration::RTCConfiguration;
use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
use webrtc::peer_connection::RTCPeerConnection;
use webrtc::track::track_local::track_local_static_rtp::TrackLocalStaticRTP;
use webrtc::track::track_local::{TrackLocal, TrackLocalWriter};
use webrtc::util::Unmarshal;

/// The relay holds the video track and manages peers.
pub struct Relay {
    video_track: Arc<TrackLocalStaticRTP>,
    _rtp_task: tokio::task::JoinHandle<()>,
    peers: Arc<Mutex<Vec<Arc<RTCPeerConnection>>>>,
    stats: Arc<PipelineStats>,
}

impl Relay {
    pub async fn new(rtp_rx: broadcast::Receiver<Vec<u8>>, stats: Arc<PipelineStats>) -> anyhow::Result<Self> {
        // Create a video track that all peers will subscribe to
        let video_track = Arc::new(TrackLocalStaticRTP::new(
            webrtc::rtp_transceiver::rtp_codec::RTCRtpCodecCapability {
                mime_type: "video/H264".to_string(),
                clock_rate: 90000,
                channels: 0,
                sdp_fmtp_line: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f".to_string(),
                rtcp_feedback: vec![],
            },
            "video".to_string(),
            "prysm-screen".to_string(),
        ));

        // Spawn task that reads RTP packets and writes to the track
        let track = video_track.clone();
        let mut rx = rtp_rx;
        let relay_stats = stats.clone();
        let rtp_task = tokio::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(packet) => {
                        let pkt_len = packet.len();
                        // Parse RTP and write to track
                        if let Ok(rtp_packet) = webrtc::rtp::packet::Packet::unmarshal(&mut packet.as_slice()) {
                            match track.write_rtp(&rtp_packet).await {
                                Ok(_) => {
                                    relay_stats.webrtc_packets_sent.inc();
                                    relay_stats.webrtc_bytes_sent.add(pkt_len as u64);
                                }
                                Err(e) => {
                                    relay_stats.webrtc_write_errors.inc();
                                    tracing::debug!("Track write error: {e}");
                                }
                            }
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        relay_stats.broadcast_lagged.inc();
                        tracing::warn!("RTP relay lagged by {n} packets");
                    }
                    Err(broadcast::error::RecvError::Closed) => {
                        tracing::info!("RTP channel closed");
                        break;
                    }
                }
            }
        });

        Ok(Self {
            video_track,
            _rtp_task: rtp_task,
            peers: Arc::new(Mutex::new(Vec::new())),
            stats,
        })
    }

    /// Handle a WHEP offer — create a peer connection and return the SDP answer.
    pub async fn handle_whep_offer(&self, offer_sdp: String) -> anyhow::Result<String> {
        let mut media_engine = MediaEngine::default();
        media_engine.register_default_codecs()?;

        let api = APIBuilder::new()
            .with_media_engine(media_engine)
            .build();

        let config = RTCConfiguration {
            ice_servers: vec![RTCIceServer {
                urls: vec!["stun:stun.l.google.com:19302".to_string()],
                ..Default::default()
            }],
            ..Default::default()
        };

        let pc = Arc::new(api.new_peer_connection(config).await?);

        // Add the video track
        pc.add_track(Arc::clone(&self.video_track) as Arc<dyn TrackLocal + Send + Sync>)
            .await?;

        // Set the remote offer
        let offer = RTCSessionDescription::offer(offer_sdp)?;
        pc.set_remote_description(offer).await?;

        // Create answer
        let answer = pc.create_answer(None).await?;
        pc.set_local_description(answer.clone()).await?;

        // Wait for ICE gathering
        let mut gather_complete = pc.gathering_complete_promise().await;
        let _ = gather_complete.recv().await;

        let local_desc = pc.local_description().await
            .ok_or_else(|| anyhow::anyhow!("No local description"))?;

        // Track the peer
        let peers = self.peers.clone();
        let pc_clone = pc.clone();
        tokio::spawn(async move {
            peers.lock().await.push(pc_clone.clone());

            // Remove peer when connection closes
            pc_clone.on_peer_connection_state_change(Box::new(move |state| {
                tracing::info!("Peer state: {state}");
                Box::pin(async {})
            }));
        });

        self.stats.whep_offers.inc();
        self.stats.webrtc_peers_total.inc();
        tracing::info!("New WebRTC viewer connected");
        Ok(local_desc.sdp)
    }
}
