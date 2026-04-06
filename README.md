# Prysm

**Split your screen everywhere.**

A [Decky Loader](https://decky.xyz/) plugin for Steam Deck that makes screen sharing dead simple — no more switching to Desktop Mode or fighting with Linux screen capture.

## Features

### Discord Go Live (Approach 1)
One-tap Discord streaming from Game Mode:
- Automatically launches Vesktop/Discord in the background
- Connects via Discord IPC
- Triggers Go Live with a single button in the QAM
- Leverages Discord's native zero-copy Gamescope capture + VAAPI encoding

### Prysm Viewer (Approach 4)
Browser-based viewer accessible from any device:
- Captures Gamescope output via PipeWire + VAAPI hardware encoding
- Serves an HLS stream on your local network
- Open the URL on any device — phone, tablet, PC
- Share that browser tab in Discord for an alternative streaming path
- Supports 480p to 1080p60 with configurable quality presets

## Install

### From Decky Plugin Store
Search for **Prysm** in the Decky plugin store (coming soon).

### Manual Install
```bash
# On your Steam Deck (Desktop Mode terminal)
mkdir -p ~/homebrew/plugins/Prysm
# Copy plugin files to that directory
# Restart Decky Loader
```

## Requirements

- **Steam Deck** running SteamOS 3.5+
- **Decky Loader** installed
- **Vesktop** (recommended) or **Discord** Flatpak for Discord Go Live mode
- **GStreamer** + **VAAPI** packages (pre-installed on SteamOS) for Viewer mode
- `xdotool` for Discord automation (install via `sudo pacman -S xdotool`)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Steam Deck (Game Mode)                                     │
│                                                             │
│  ┌─────────┐    ┌──────────────┐    ┌───────────────────┐  │
│  │Gamescope│───→│ PipeWire     │───→│ GStreamer Pipeline │  │
│  │(display)│    │ (capture)    │    │ VAAPI H.264 enc   │  │
│  └─────────┘    └──────────────┘    └─────────┬─────────┘  │
│                                               │             │
│                          ┌────────────────────┼──────┐      │
│                          │                    │      │      │
│                     ┌────▼─────┐    ┌─────────▼──┐   │      │
│                     │ Discord  │    │ HLS HTTP   │   │      │
│                     │ Go Live  │    │ Server     │   │      │
│                     │ (native) │    │ :7770      │   │      │
│                     └──────────┘    └─────┬──────┘   │      │
│                                           │          │      │
│  ┌──────────────────┐                     │          │      │
│  │ Decky QAM Panel  │ ◄── controls ──────┘          │      │
│  │ (React/TypeScript)│                               │      │
│  └──────────────────┘                                │      │
│                                                      │      │
└──────────────────────────────────────────────────────┘      │
                                                              │
               ┌──────────────────────────────────────────────┘
               │
        ┌──────▼──────┐
        │ Any Browser │  phone / tablet / PC
        │ viewer.html │  → share this tab in Discord!
        └─────────────┘
```

## QAM Panel

The plugin adds a panel to Steam Deck's Quick Access Menu with three tabs:

| Tab | Description |
|-----|-------------|
| **Discord** | Launch Discord, connect IPC, toggle Go Live |
| **Viewer** | Start/stop Prysm Viewer, quality settings, viewer URL |
| **Settings** | Preferred Discord client, capture method |

## Development

```bash
# Clone
git clone https://github.com/hostsrc/decky-prysm.git
cd decky-prysm

# Install dependencies
pnpm install

# Build frontend
pnpm build

# Watch mode
pnpm watch
```

### Testing on Steam Deck
```bash
# SSH into your Steam Deck
scp -r dist/ main.py plugin.json package.json assets/ defaults/ \
  deck@steamdeck:~/homebrew/plugins/Prysm/

# Restart Decky Loader
ssh deck@steamdeck "sudo systemctl restart plugin_loader"
```

## Roadmap

- [ ] Discord Go Live automation via Vesktop
- [ ] HLS-based Prysm Viewer
- [ ] WebRTC viewer (lower latency replacement for HLS)
- [ ] QR code generation for easy mobile access
- [ ] Voice channel picker via Discord IPC
- [ ] Password-protected viewer streams
- [ ] OBS RTMP output mode
- [ ] Decky Plugin Store submission

## Credits

Built on the shoulders of:
- [decky-streamer](https://github.com/deamos/decky-streamer) — GStreamer capture pipeline patterns
- [chromecast-decky-plugin](https://github.com/lufinkey/chromecast-decky-plugin) — KMS/DRM capture reference
- [steamdeck-discord-status](https://github.com/andrewburgess/steamdeck-discord-status) — Discord IPC reference

## License

GPL-2.0-or-later
