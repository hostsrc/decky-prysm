//! WebRTC relay — receives RTP packets from FFmpeg and fans them out
//! to all connected WebRTC peers via WHEP (WebRTC-HTTP Egress Protocol).
//!
//! Key fix: webrtc-rs TrackLocalStaticRTP.write_rtp() rewrites SSRC and
//! payload type to match the negotiated WebRTC session. We just need to
//! parse FFmpeg's RTP and feed it through — the library handles the rest.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use tokio::sync::{broadcast, Mutex};
use webrtc::api::interceptor_registry::register_default_interceptors;
use webrtc::api::media_engine::MediaEngine;
use webrtc::api::APIBuilder;
use webrtc::ice_transport::ice_server::RTCIceServer;
use webrtc::interceptor::registry::Registry;
use webrtc::peer_connection::configuration::RTCConfiguration;
use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
use webrtc::peer_connection::RTCPeerConnection;
use webrtc::rtp_transceiver::rtp_codec::{RTCRtpCodecCapability, RTCRtpCodecParameters};
use webrtc::track::track_local::track_local_static_rtp::TrackLocalStaticRTP;
use webrtc::track::track_local::{TrackLocal, TrackLocalWriter};
use webrtc::util::Unmarshal;

use crate::stats::PipelineStats;

/// H.264 NALU type constants
const NALU_TYPE_IDR: u8 = 5;   // Instantaneous Decoder Refresh (keyframe)
const NALU_TYPE_SPS: u8 = 7;   // Sequence Parameter Set
const NALU_TYPE_PPS: u8 = 8;   // Picture Parameter Set
const NALU_TYPE_FUA: u8 = 28;  // Fragmentation Unit A

/// The relay holds the video track and manages peers.
pub struct Relay {
    video_track: Arc<TrackLocalStaticRTP>,
    _rtp_task: tokio::task::JoinHandle<()>,
    peers: Arc<Mutex<Vec<Arc<RTCPeerConnection>>>>,
    stats: Arc<PipelineStats>,
}

/// Detect if an RTP packet contains a keyframe (IDR/SPS/PPS)
fn is_keyframe_packet(payload: &[u8]) -> bool {
    if payload.is_empty() {
        return false;
    }
    let nalu_type = payload[0] & 0x1F;
    match nalu_type {
        NALU_TYPE_IDR | NALU_TYPE_SPS | NALU_TYPE_PPS => true,
        NALU_TYPE_FUA if payload.len() > 1 => {
            // FU-A: check if it's the start of an IDR fragment
            let start_bit = (payload[1] & 0x80) != 0;
            let fragment_type = payload[1] & 0x1F;
            start_bit && fragment_type == NALU_TYPE_IDR
        }
        _ => false,
    }
}

impl Relay {
    pub async fn new(rtp_rx: broadcast::Receiver<Vec<u8>>, stats: Arc<PipelineStats>) -> anyhow::Result<Self> {
        // Create a video track — use High Profile to match h264_vaapi output
        // Profile 42001f = Baseline, 4d001f = Main, 640028 = High Level 4.0
        let video_track = Arc::new(TrackLocalStaticRTP::new(
            RTCRtpCodecCapability {
                mime_type: "video/H264".to_string(),
                clock_rate: 90000,
                channels: 0,
                sdp_fmtp_line: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640028".to_string(),
                rtcp_feedback: vec![
                    // Allow browser to request keyframes via PLI/FIR
                    webrtc::rtp_transceiver::RTCPFeedback {
                        typ: "nack".to_string(),
                        parameter: "".to_string(),
                    },
                    webrtc::rtp_transceiver::RTCPFeedback {
                        typ: "nack".to_string(),
                        parameter: "pli".to_string(),
                    },
                ],
            },
            "video".to_string(),
            "prysm-screen".to_string(),
        ));

        // Counter for diagnostic logging (first N packets)
        let diag_counter = Arc::new(AtomicU64::new(0));
        let keyframe_counter = Arc::new(AtomicU64::new(0));

        // Spawn task that reads RTP packets and writes to the track
        let track = video_track.clone();
        let mut rx = rtp_rx;
        let relay_stats = stats.clone();
        let diag = diag_counter.clone();
        let kf_count = keyframe_counter.clone();

        let rtp_task = tokio::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(packet) => {
                        let pkt_len = packet.len();

                        // Parse RTP packet
                        let rtp_packet = match webrtc::rtp::packet::Packet::unmarshal(
                            &mut packet.as_slice(),
                        ) {
                            Ok(p) => p,
                            Err(e) => {
                                tracing::debug!("RTP unmarshal error: {e}");
                                continue;
                            }
                        };

                        // Diagnostic: log first 5 packets and keyframes
                        let count = diag.fetch_add(1, Ordering::Relaxed);
                        if count < 5 {
                            tracing::info!(
                                "RTP #{}: pt={} ssrc={} seq={} ts={} payload_len={}",
                                count,
                                rtp_packet.header.payload_type,
                                rtp_packet.header.ssrc,
                                rtp_packet.header.sequence_number,
                                rtp_packet.header.timestamp,
                                rtp_packet.payload.len(),
                            );
                        }

                        // Detect keyframes for logging
                        if is_keyframe_packet(&rtp_packet.payload) {
                            let kf = kf_count.fetch_add(1, Ordering::Relaxed);
                            if kf < 10 || kf % 30 == 0 {
                                tracing::info!(
                                    "Keyframe #{kf} detected (seq={}, ts={})",
                                    rtp_packet.header.sequence_number,
                                    rtp_packet.header.timestamp,
                                );
                            }
                        }

                        // Write to the track — webrtc-rs handles SSRC/PT rewriting
                        match track.write_rtp(&rtp_packet).await {
                            Ok(n) => {
                                relay_stats.webrtc_packets_sent.inc();
                                relay_stats.webrtc_bytes_sent.add(pkt_len as u64);
                                if count < 5 {
                                    tracing::info!("write_rtp returned Ok({n}) bytes written");
                                }
                            }
                            Err(e) => {
                                relay_stats.webrtc_write_errors.inc();
                                if count < 20 {
                                    tracing::warn!("Track write error: {e}");
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
        // Register H.264 codec with matching profile
        let mut media_engine = MediaEngine::default();

        // Register H.264 High Profile specifically
        media_engine.register_codec(
            RTCRtpCodecParameters {
                capability: RTCRtpCodecCapability {
                    mime_type: "video/H264".to_string(),
                    clock_rate: 90000,
                    channels: 0,
                    sdp_fmtp_line: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640028".to_string(),
                    rtcp_feedback: vec![
                        webrtc::rtp_transceiver::RTCPFeedback {
                            typ: "nack".to_string(),
                            parameter: "".to_string(),
                        },
                        webrtc::rtp_transceiver::RTCPFeedback {
                            typ: "nack".to_string(),
                            parameter: "pli".to_string(),
                        },
                    ],
                },
                payload_type: 102,
                ..Default::default()
            },
            webrtc::rtp_transceiver::rtp_codec::RTPCodecType::Video,
        )?;

        // Also register default codecs as fallback
        media_engine.register_default_codecs()?;

        // Register interceptors for RTCP feedback (PLI, NACK)
        let mut registry = Registry::new();
        registry = register_default_interceptors(registry, &mut media_engine)?;

        let api = APIBuilder::new()
            .with_media_engine(media_engine)
            .with_interceptor_registry(registry)
            .build();

        let config = RTCConfiguration {
            ice_servers: vec![RTCIceServer {
                urls: vec!["stun:stun.l.google.com:19302".to_string()],
                ..Default::default()
            }],
            ..Default::default()
        };

        let pc = Arc::new(api.new_peer_connection(config).await?);

        // Log ICE and connection state changes
        let pc_for_ice = pc.clone();
        pc.on_ice_connection_state_change(Box::new(move |state| {
            tracing::info!("ICE state: {state}");
            Box::pin(async {})
        }));

        pc.on_peer_connection_state_change(Box::new(move |state| {
            tracing::info!("Peer state: {state}");
            Box::pin(async {})
        }));

        // Add the video track
        let rtp_sender = pc
            .add_track(Arc::clone(&self.video_track) as Arc<dyn TrackLocal + Send + Sync>)
            .await?;

        // Read RTCP from the browser (PLI requests, receiver reports)
        // This is required to keep the WebRTC connection alive
        tokio::spawn(async move {
            loop {
                match rtp_sender.read_rtcp().await {
                    Ok((pkts, _)) => {
                        tracing::debug!("RTCP from browser: {} packets", pkts.len());
                    }
                    Err(_) => {
                        tracing::debug!("RTCP read done");
                        break;
                    }
                }
            }
        });

        // Set the remote offer
        let offer = RTCSessionDescription::offer(offer_sdp)?;
        pc.set_remote_description(offer).await?;

        // Create answer
        let answer = pc.create_answer(None).await?;
        pc.set_local_description(answer).await?;

        // Wait for ICE gathering to complete
        let mut gather_complete = pc.gathering_complete_promise().await;
        let _ = gather_complete.recv().await;

        let local_desc = pc
            .local_description()
            .await
            .ok_or_else(|| anyhow::anyhow!("No local description after ICE gathering"))?;

        tracing::info!("WHEP answer SDP ready ({} bytes)", local_desc.sdp.len());

        // Track the peer
        self.peers.lock().await.push(pc.clone());
        self.stats.whep_offers.inc();
        self.stats.webrtc_peers_total.inc();

        tracing::info!("New WebRTC viewer connected (total peers: {})", self.stats.webrtc_peers_total.get());
        Ok(local_desc.sdp)
    }
}
