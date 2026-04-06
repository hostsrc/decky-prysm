"""
Prysm — Split your screen everywhere.
Decky Loader plugin for Steam Deck screen sharing.

Two modes:
  1. Discord Go Live — automates Vesktop/Discord launch + Go Live trigger
  2. Prysm Viewer   — WebRTC-based viewer accessible from any browser
"""

import asyncio
import json
import os
import signal
import socket
import struct
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional

import decky


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VESKTOP_FLATPAK = "dev.vencord.Vesktop"
DISCORD_FLATPAK = "com.discordapp.Discord"
IPC_SOCKET_PATHS = [
    "/run/user/1000/discord-ipc-0",
    "/run/user/1000/app/com.discordapp.Discord/discord-ipc-0",
    "/run/user/1000/app/dev.vencord.Vesktop/discord-ipc-0",
]

VIEWER_PORT = 7770
SIGNALING_PORT = 7771


class StreamMode(str, Enum):
    DISCORD = "discord"
    VIEWER = "viewer"
    IDLE = "idle"


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

class Settings:
    def __init__(self, settings_dir: str) -> None:
        self._path = Path(settings_dir) / "prysm.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            with open(self._path) as f:
                return json.load(f)
        return self._defaults()

    @staticmethod
    def _defaults() -> dict:
        return {
            "preferred_client": "vesktop",
            "auto_join_channel": "",
            "viewer_quality": "720p30",
            "viewer_bitrate": 5000,
            "viewer_password": "",
            "capture_method": "pipewire",
            "audio_enabled": True,
            "last_voice_channel_id": "",
        }

    def get(self, key: str, fallback: object = None) -> object:
        return self._data.get(key, fallback)

    def set(self, key: str, value: object) -> None:
        self._data[key] = value
        self._save()

    def all(self) -> dict:
        return {**self._data}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)


# ---------------------------------------------------------------------------
# Discord IPC client (read-only presence + Go Live trigger helper)
# ---------------------------------------------------------------------------

class DiscordIPC:
    """Minimal Discord IPC client for local RPC communication."""

    OP_HANDSHAKE = 0
    OP_FRAME = 1

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def find_socket(self) -> Optional[str]:
        for path in IPC_SOCKET_PATHS:
            if os.path.exists(path):
                return path
        # Scan for any discord-ipc socket under XDG_RUNTIME_DIR
        runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")
        for i in range(10):
            candidate = os.path.join(runtime, f"discord-ipc-{i}")
            if os.path.exists(candidate):
                return candidate
        return None

    def connect(self, client_id: str = "1") -> bool:
        sock_path = self.find_socket()
        if not sock_path:
            decky.logger.warning("No Discord IPC socket found")
            return False

        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(sock_path)
            # Handshake
            payload = json.dumps({"v": 1, "client_id": client_id})
            self._send(self.OP_HANDSHAKE, payload)
            _op, data = self._recv()
            if data and data.get("cmd") == "DISPATCH":
                self._connected = True
                decky.logger.info(f"Discord IPC connected via {sock_path}")
                return True
        except Exception as e:
            decky.logger.error(f"Discord IPC connect failed: {e}")
        return False

    def get_voice_state(self) -> Optional[dict]:
        if not self._connected:
            return None
        try:
            self._send(self.OP_FRAME, json.dumps({
                "cmd": "GET_SELECTED_VOICE_CHANNEL",
                "nonce": "prysm-voice-state",
                "args": {},
            }))
            _op, data = self._recv()
            return data
        except Exception as e:
            decky.logger.error(f"Failed to get voice state: {e}")
            return None

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._connected = False

    def _send(self, op: int, payload: str) -> None:
        encoded = payload.encode("utf-8")
        header = struct.pack("<II", op, len(encoded))
        self._sock.sendall(header + encoded)

    def _recv(self) -> tuple:
        header = self._sock.recv(8)
        if len(header) < 8:
            return -1, None
        op, length = struct.unpack("<II", header)
        data = b""
        while len(data) < length:
            chunk = self._sock.recv(length - len(data))
            if not chunk:
                break
            data += chunk
        try:
            return op, json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return op, None


# ---------------------------------------------------------------------------
# Capture pipeline manager (GStreamer + PipeWire/kmsgrab)
# ---------------------------------------------------------------------------

class CapturePipeline:
    """Manages GStreamer capture pipeline for the WebRTC viewer mode."""

    QUALITY_PRESETS = {
        "480p30":  {"width": 854,  "height": 480,  "fps": 30, "bitrate": 2500},
        "720p30":  {"width": 1280, "height": 720,  "fps": 30, "bitrate": 5000},
        "720p60":  {"width": 1280, "height": 720,  "fps": 60, "bitrate": 8000},
        "1080p30": {"width": 1920, "height": 1080, "fps": 30, "bitrate": 8000},
        "1080p60": {"width": 1920, "height": 1080, "fps": 60, "bitrate": 12000},
    }

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None

    def start(self, quality: str = "720p30", audio: bool = True) -> bool:
        if self.running:
            decky.logger.warning("Capture pipeline already running")
            return True

        preset = self.QUALITY_PRESETS.get(quality, self.QUALITY_PRESETS["720p30"])

        pipeline = self._build_pipeline(preset, audio)
        decky.logger.info(f"Starting capture: {quality} pipeline")

        try:
            env = os.environ.copy()
            env["DISPLAY"] = ":1"  # Gamescope game display
            env["XDG_RUNTIME_DIR"] = "/run/user/1000"

            self._process = subprocess.Popen(
                pipeline,
                shell=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            self._running = True
            decky.logger.info(f"Capture pipeline started (PID {self._process.pid})")
            return True
        except Exception as e:
            decky.logger.error(f"Failed to start capture: {e}")
            return False

    def stop(self) -> None:
        if self._process:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                self._process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self._process = None
        self._running = False
        decky.logger.info("Capture pipeline stopped")

    def _build_pipeline(self, preset: dict, audio: bool) -> str:
        """Build a GStreamer pipeline that outputs to an HTTP endpoint."""
        w, h, fps, bitrate = preset["width"], preset["height"], preset["fps"], preset["bitrate"]

        # Video: PipeWire source → scale → VAAPI H.264 encode
        video = (
            f"gst-launch-1.0 -e "
            f"pipewiresrc do-timestamp=true ! "
            f"videoconvert ! "
            f"videoscale ! video/x-raw,width={w},height={h} ! "
            f"videorate ! video/x-raw,framerate={fps}/1 ! "
            f"vaapih264enc bitrate={bitrate} tune=low-power ! "
            f"video/x-h264,profile=main ! "
            f"h264parse ! "
            f"mpegtsmux name=mux ! "
            f"hlssink location=/tmp/prysm/segment_%05d.ts "
            f"playlist-location=/tmp/prysm/stream.m3u8 "
            f"target-duration=2 max-files=5 "
        )

        if audio:
            # Add audio from PulseAudio default monitor
            video = video.replace(
                "mpegtsmux name=mux",
                "mpegtsmux name=mux "
                "pulsesrc device=$(pactl get-default-sink).monitor ! "
                "audioconvert ! audioresample ! "
                "audio/x-raw,rate=48000,channels=2 ! "
                "avenc_aac bitrate=128000 ! "
                "aacparse ! mux."
            )

        return video

    @staticmethod
    def ensure_output_dir() -> None:
        os.makedirs("/tmp/prysm", exist_ok=True)


# ---------------------------------------------------------------------------
# Lightweight HTTP server for WebRTC viewer
# ---------------------------------------------------------------------------

class ViewerServer:
    """Simple HTTP server that serves the viewer page and HLS stream."""

    def __init__(self, port: int = VIEWER_PORT) -> None:
        self._port = port
        self._process: Optional[subprocess.Popen] = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        if self.running:
            return True

        self._write_viewer_page()

        try:
            self._process = subprocess.Popen(
                [
                    "python3", "-m", "http.server",
                    str(self._port),
                    "--directory", "/tmp/prysm",
                    "--bind", "0.0.0.0",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            decky.logger.info(f"Viewer server started on port {self._port}")
            return True
        except Exception as e:
            decky.logger.error(f"Failed to start viewer server: {e}")
            return False

    def stop(self) -> None:
        if self._process:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                self._process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self._process = None
        decky.logger.info("Viewer server stopped")

    def get_url(self) -> str:
        ip = self._get_local_ip()
        return f"http://{ip}:{self._port}/viewer.html"

    @staticmethod
    def _get_local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "localhost"

    @staticmethod
    def _write_viewer_page() -> None:
        os.makedirs("/tmp/prysm", exist_ok=True)
        viewer_html = Path(decky.DECKY_PLUGIN_DIR) / "assets" / "viewer.html"
        dest = Path("/tmp/prysm/viewer.html")
        if viewer_html.exists():
            dest.write_text(viewer_html.read_text())
        else:
            # Fallback inline viewer
            dest.write_text(VIEWER_HTML_FALLBACK)


# Inline fallback viewer page — see assets/viewer.html for the full version
VIEWER_HTML_FALLBACK = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prysm Viewer</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0e0;font-family:system-ui,-apple-system,sans-serif;overflow:hidden;height:100vh}
#player{width:100vw;height:100vh;object-fit:contain;background:#000}
.overlay{position:fixed;top:0;left:0;right:0;padding:1rem;background:linear-gradient(to bottom,rgba(0,0,0,.7),transparent);z-index:10;display:flex;align-items:center;gap:1rem;opacity:0;transition:opacity .3s}
.overlay:hover{opacity:1}
.prysm-logo{font-size:1.25rem;font-weight:700;background:linear-gradient(135deg,#a855f7,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.status{font-size:.85rem;opacity:.7}
</style>
</head>
<body>
<div class="overlay">
  <span class="prysm-logo">PRYSM</span>
  <span class="status" id="status">Connecting...</span>
</div>
<video id="player" autoplay muted playsinline></video>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<script>
const video = document.getElementById('player');
const status = document.getElementById('status');
const src = '/stream.m3u8';

if (Hls.isSupported()) {
  const hls = new Hls({ liveSyncDuration: 1, liveMaxLatencyDuration: 3 });
  hls.loadSource(src);
  hls.attachMedia(video);
  hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play(); status.textContent = 'Live'; });
  hls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) { status.textContent = 'Reconnecting...'; setTimeout(() => hls.loadSource(src), 2000); }});
} else if (video.canPlayType('application/vnd.apple.mpegurl')) {
  video.src = src;
  video.addEventListener('loadedmetadata', () => { video.play(); status.textContent = 'Live'; });
}

// Unmute on click
video.addEventListener('click', () => { video.muted = !video.muted; });
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Vesktop / Discord process manager
# ---------------------------------------------------------------------------

class VesktopManager:
    """Manages launching and detecting Vesktop/Discord."""

    def __init__(self, preferred: str = "vesktop") -> None:
        self._preferred = preferred
        self._process: Optional[subprocess.Popen] = None

    def is_running(self) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "vesktop|Discord|discord"],
                capture_output=True, text=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def launch(self) -> bool:
        if self.is_running():
            decky.logger.info("Discord/Vesktop already running")
            return True

        flatpak_id = VESKTOP_FLATPAK if self._preferred == "vesktop" else DISCORD_FLATPAK

        try:
            # Check if flatpak is installed
            check = subprocess.run(
                ["flatpak", "info", flatpak_id],
                capture_output=True, text=True
            )
            if check.returncode != 0:
                decky.logger.error(f"{flatpak_id} not installed")
                return False

            self._process = subprocess.Popen(
                ["flatpak", "run", flatpak_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
                env={
                    **os.environ,
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                    "DISPLAY": ":0",
                },
            )
            decky.logger.info(f"Launched {flatpak_id}")
            return True
        except Exception as e:
            decky.logger.error(f"Failed to launch {flatpak_id}: {e}")
            return False

    def kill(self) -> None:
        if self._process:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            self._process = None


# ---------------------------------------------------------------------------
# Main Plugin class
# ---------------------------------------------------------------------------

class Plugin:
    settings: Settings
    discord_ipc: DiscordIPC
    capture: CapturePipeline
    viewer: ViewerServer
    vesktop: VesktopManager
    current_mode: StreamMode

    # -- Lifecycle ----------------------------------------------------------

    async def _main(self) -> None:
        decky.logger.info("Prysm starting up")
        self.settings = Settings(decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.discord_ipc = DiscordIPC()
        self.capture = CapturePipeline()
        self.viewer = ViewerServer(VIEWER_PORT)
        self.vesktop = VesktopManager(self.settings.get("preferred_client", "vesktop"))
        self.current_mode = StreamMode.IDLE
        CapturePipeline.ensure_output_dir()
        decky.logger.info("Prysm ready")

    async def _unload(self) -> None:
        decky.logger.info("Prysm shutting down")
        await self.stop_all()
        self.discord_ipc.disconnect()

    # -- Settings -----------------------------------------------------------

    async def get_settings(self) -> dict:
        return self.settings.all()

    async def set_setting(self, key: str, value: object) -> None:
        self.settings.set(key, value)

    # -- Status -------------------------------------------------------------

    async def get_status(self) -> dict:
        discord_running = self.vesktop.is_running()
        discord_ipc_ok = self.discord_ipc.connected
        viewer_running = self.viewer.running
        capture_running = self.capture.running

        viewer_url = self.viewer.get_url() if viewer_running else ""

        return {
            "mode": self.current_mode.value,
            "discord_running": discord_running,
            "discord_ipc": discord_ipc_ok,
            "viewer_running": viewer_running,
            "viewer_url": viewer_url,
            "capture_running": capture_running,
        }

    # -- Discord Go Live (Approach 1) ---------------------------------------

    async def discord_launch(self) -> dict:
        """Launch Vesktop/Discord and connect IPC."""
        ok = self.vesktop.launch()
        if not ok:
            return {"success": False, "error": "Failed to launch Discord client"}

        # Wait for IPC socket to appear
        for _ in range(15):
            await asyncio.sleep(1)
            if self.discord_ipc.connect():
                break

        return {
            "success": self.discord_ipc.connected,
            "error": "" if self.discord_ipc.connected else "Discord IPC not available yet — try again in a few seconds",
        }

    async def discord_get_voice(self) -> dict:
        """Get current voice channel state."""
        state = self.discord_ipc.get_voice_state()
        return {"success": state is not None, "data": state}

    async def discord_go_live(self) -> dict:
        """
        Trigger Go Live in Discord.

        Since Discord's IPC doesn't expose a direct "start stream" command,
        we use xdotool to simulate the keyboard shortcut (Ctrl+Shift+G)
        which is Discord's default Go Live toggle.
        """
        if not self.vesktop.is_running():
            return {"success": False, "error": "Discord is not running"}

        try:
            # Focus the Discord window and send Go Live shortcut
            subprocess.run(
                ["xdotool", "search", "--name", "Discord", "windowactivate", "--sync"],
                capture_output=True, timeout=5,
            )
            await asyncio.sleep(0.5)
            subprocess.run(
                ["xdotool", "key", "ctrl+shift+g"],
                capture_output=True, timeout=3,
            )
            self.current_mode = StreamMode.DISCORD
            await decky.emit("mode_changed", StreamMode.DISCORD.value)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def discord_stop_live(self) -> dict:
        """Stop Go Live by sending the same toggle shortcut."""
        try:
            subprocess.run(
                ["xdotool", "search", "--name", "Discord", "windowactivate", "--sync"],
                capture_output=True, timeout=5,
            )
            await asyncio.sleep(0.5)
            subprocess.run(
                ["xdotool", "key", "ctrl+shift+g"],
                capture_output=True, timeout=3,
            )
            self.current_mode = StreamMode.IDLE
            await decky.emit("mode_changed", StreamMode.IDLE.value)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -- Prysm Viewer (Approach 4) ------------------------------------------

    async def viewer_start(self) -> dict:
        """Start the WebRTC/HLS viewer with capture pipeline."""
        quality = self.settings.get("viewer_quality", "720p30")
        audio = self.settings.get("audio_enabled", True)

        CapturePipeline.ensure_output_dir()

        if not self.capture.start(quality, audio):
            return {"success": False, "error": "Failed to start capture pipeline"}

        if not self.viewer.start():
            self.capture.stop()
            return {"success": False, "error": "Failed to start viewer server"}

        self.current_mode = StreamMode.VIEWER
        url = self.viewer.get_url()
        await decky.emit("mode_changed", StreamMode.VIEWER.value)
        await decky.emit("viewer_url", url)

        decky.logger.info(f"Prysm Viewer live at {url}")
        return {"success": True, "url": url}

    async def viewer_stop(self) -> dict:
        """Stop the viewer and capture pipeline."""
        self.capture.stop()
        self.viewer.stop()
        self.current_mode = StreamMode.IDLE
        await decky.emit("mode_changed", StreamMode.IDLE.value)
        return {"success": True}

    async def viewer_get_url(self) -> str:
        return self.viewer.get_url() if self.viewer.running else ""

    # -- Shared controls ----------------------------------------------------

    async def stop_all(self) -> dict:
        """Stop everything."""
        self.capture.stop()
        self.viewer.stop()
        self.current_mode = StreamMode.IDLE
        await decky.emit("mode_changed", StreamMode.IDLE.value)
        return {"success": True}

    async def get_network_info(self) -> dict:
        """Get local network info for viewer URL display."""
        ip = ViewerServer._get_local_ip()
        return {
            "ip": ip,
            "viewer_port": VIEWER_PORT,
            "viewer_url": f"http://{ip}:{VIEWER_PORT}/viewer.html",
        }
