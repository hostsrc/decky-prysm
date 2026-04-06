# Prysm

**Split your screen everywhere.**

A [Decky Loader](https://decky.xyz/) plugin for Steam Deck that streams your screen to any device via WebRTC — low latency, hardware accelerated, no Desktop Mode required.

## Architecture

```
Steam Deck (Game Mode)
┌──────────────────────────────────────────────────────────┐
│                                                          │
│  Gamescope ──→ kmsgrab ──→ FFmpeg (VAAPI H.264) ──→ RTP │
│                                                     │    │
│                                                     ▼    │
│                              prysm-engine (Rust, 4.8MB)  │
│                              ├─ RTP relay                │
│                              ├─ WebRTC (WHEP)            │
│                              ├─ /stats profiler          │
│                              └─ Viewer page              │
│                                     │                    │
│  Decky QAM Panel ◄── start/stop ───┘                    │
│  (React/TypeScript)                                      │
│                                                          │
└──────────────────────────────┬───────────────────────────┘
                               │ WebRTC
                               ▼
                        ┌──────────────┐
                        │ Any Browser  │  phone / tablet / PC
                        │ :7770        │  → share tab in Discord
                        └──────────────┘
```

### Streaming Pipeline

| Stage | Technology | Latency |
|-------|-----------|---------|
| Capture | `kmsgrab` (DRM framebuffer) | ~0ms |
| Encode | `h264_vaapi` (AMD GPU hardware) | ~3ms |
| Transport | RTP → `prysm-engine` → WebRTC | ~50ms |
| Decode | Browser hardware H.264 | ~5ms |
| **Total** | | **~100-300ms target** |

### Key Design Decisions

- **Rust engine** (`prysm-engine`) handles all real-time work — zero Python in the streaming hot path
- **FFmpeg** for capture + encode (will be replaced with native PipeWire + VAAPI in v2)
- **Auto-restart** on Gamescope framebuffer format changes (QAM overlay, notifications)
- **WHEP** (WebRTC-HTTP Egress Protocol) for browser-native low-latency playback
- **Stats profiler** at `/stats` for real-time pipeline diagnostics

## Features

### Prysm Viewer (WebRTC)
- One-tap start from Steam Deck QAM
- Hardware-accelerated H.264 encoding via VAAPI
- WebRTC streaming to any browser on the local network
- Quality presets: 480p30 → 1080p60
- Real-time pipeline stats at `http://DECK_IP:7770/stats`
- Auto-restart on capture failures

### Discord Go Live
- Automates Vesktop/Discord launch from Game Mode
- Discord IPC connection for voice channel detection
- One-button Go Live trigger via QAM

## Install

### One-liner (on Steam Deck)
```bash
curl -sL https://raw.githubusercontent.com/hostsrc/decky-prysm/main/install.sh | bash
```

### Manual Install
```bash
git clone https://github.com/hostsrc/decky-prysm.git /tmp/prysm
sudo mkdir -p ~/homebrew/plugins/Prysm
sudo cp -r /tmp/prysm/{main.py,plugin.json,package.json,dist,assets,defaults} \
  ~/homebrew/plugins/Prysm/
sudo cp -r /tmp/prysm/backend/prysm-engine/target/x86_64-unknown-linux-musl/release/prysm-engine \
  ~/homebrew/plugins/Prysm/bin/
sudo systemctl restart plugin_loader
```

## Requirements

- **Steam Deck** running SteamOS 3.5+
- **Decky Loader** installed
- **FFmpeg** with VAAPI support (pre-installed on SteamOS)
- **Vesktop** Flatpak (optional, for Discord Go Live mode)

## Development

### Frontend (TypeScript/React)
```bash
pnpm install
pnpm build      # Build Decky frontend
pnpm watch      # Watch mode
```

### Backend (Rust engine)
```bash
cd backend/prysm-engine

# Native build (for local testing)
cargo build --release

# Cross-compile for Steam Deck
cargo zigbuild --release --target x86_64-unknown-linux-musl
```

### Deploy to Steam Deck
```bash
./deploy.sh 192.168.88.196    # your Deck's IP
```

### Project Structure
```
decky-prysm/
├── src/                          # Decky frontend (TypeScript/React)
│   ├── index.tsx                 # Plugin root — QAM panel
│   ├── components/               # Discord, Viewer, Settings panels
│   ├── hooks/                    # Status polling hook
│   └── lib/backend.ts            # Typed Python backend callables
├── main.py                       # Decky Python backend (process manager)
├── backend/
│   └── prysm-engine/             # Rust streaming engine
│       └── src/
│           ├── main.rs           # CLI + startup
│           ├── capture.rs        # FFmpeg supervisor + RTP receiver
│           ├── webrtc_relay.rs   # RTP → WebRTC relay (WHEP)
│           ├── server.rs         # HTTP server (viewer + /whep + /stats)
│           └── stats.rs          # Pipeline performance profiler
├── assets/
│   ├── viewer-webrtc.html        # Browser WebRTC viewer page
│   └── viewer.html               # Legacy HLS viewer (deprecated)
├── install.sh                    # One-liner installer for Steam Deck
├── deploy.sh                     # Dev deploy script (Mac → Deck)
└── plugin.json                   # Decky plugin metadata
```

## Stats Profiler

The engine exposes real-time pipeline metrics at `http://DECK_IP:7770/stats`:

```json
{
  "uptime_secs": 184.17,
  "ffmpeg_restarts": 4,
  "ffmpeg_format_changes": 4,
  "rtp_pps": 753.94,
  "rtp_mbps": 8.0,
  "rtp_total_packets": 138240,
  "rtp_errors": 0,
  "broadcast_lagged": 0,
  "webrtc_pps": 753.94,
  "webrtc_mbps": 8.0,
  "webrtc_peers_total": 1,
  "whep_offers": 1,
  "whep_errors": 0
}
```

Stats are also logged to stdout every 5 seconds when running the engine directly.

## Roadmap

- [x] Decky plugin with QAM panel (Discord + Viewer + Settings tabs)
- [x] FFmpeg kmsgrab capture with VAAPI H.264 encoding
- [x] Auto-restart on Gamescope format changes
- [x] Rust streaming engine (`prysm-engine`) with WebRTC relay
- [x] WHEP signaling server
- [x] Pipeline stats profiler (`/stats` endpoint)
- [x] Cross-compilation for Steam Deck (x86_64-linux-musl)
- [ ] **Fix RTP header rewriting** (SSRC mismatch between FFmpeg and WebRTC track)
- [ ] Native PipeWire capture (replace kmsgrab with Gamescope's PipeWire stream)
- [ ] Native VAAPI encoding in Rust (eliminate FFmpeg dependency)
- [ ] Audio support (separate WebRTC audio track)
- [ ] Discord Go Live automation via Vesktop
- [ ] QR code for easy mobile viewer access
- [ ] Decky Plugin Store submission

## Gamescope Internals

Gamescope exposes a PipeWire `Video/Source` stream (node name: `gamescope`) that provides:
- DMA-BUF frames (zero-copy GPU memory)
- BGRx or NV12 format at native resolution
- Handles format renegotiation on overlay/resolution changes
- This is the official capture API — used by OBS and other tools

The v2 engine will use this directly instead of `kmsgrab`, eliminating the format-change restart issue entirely.

See: [ValveSoftware/gamescope](https://github.com/ValveSoftware/gamescope) — `src/pipewire.cpp`, `protocol/gamescope-pipewire.xml`

## Credits

- [Gamescope](https://github.com/ValveSoftware/gamescope) — Valve's Wayland compositor, PipeWire streaming internals
- [webrtc-rs](https://github.com/webrtc-rs/webrtc) — Pure Rust WebRTC stack
- [decky-streamer](https://github.com/deamos/decky-streamer) — GStreamer capture pipeline patterns
- [chromecast-decky-plugin](https://github.com/lufinkey/chromecast-decky-plugin) — KMS/DRM capture reference
- [steamdeck-discord-status](https://github.com/andrewburgess/steamdeck-discord-status) — Discord IPC reference

## License

GPL-2.0-or-later
