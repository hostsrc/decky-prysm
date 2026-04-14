"""
Prysm - Split your screen everywhere.
Decky Loader plugin for Steam Deck screen sharing.

Two modes:
  1. Discord Go Live - automates Vesktop/Discord launch + Go Live trigger
  2. Prysm Viewer   - WebRTC-based viewer accessible from any browser
"""

import asyncio
import glob
import json
import os
import signal
import socket
import struct
import subprocess
import time
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
            "stream_method": "mpegts",
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
# Streaming engine: stream_server.py (HTTP MPEG-TS)
# ---------------------------------------------------------------------------

class StreamEngine:
    """Manages the stream server (MPEG-TS or MediaMTX WebRTC).

    MPEG-TS: kmsgrab → FFmpeg → MPEG-TS → HTTP → mpegts.js (~500ms)
    WebRTC:  kmsgrab → FFmpeg → RTSP → MediaMTX → WebRTC (~200ms)
    """

    MPEGTS_PORT = 7770
    WEBRTC_PORT = 8889
    MEDIAMTX_BIN = None
    MEDIAMTX_CFG = None

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._running = False
        self._method = "mpegts"
        plugin_dir = Path(decky.DECKY_PLUGIN_DIR)
        self._server_script = str(plugin_dir / "server/stream_server.py")
        self.MEDIAMTX_BIN = str(plugin_dir / "bin" / "mediamtx")
        self.MEDIAMTX_CFG = str(plugin_dir / "bin" / "mediamtx.yml")
        decky.logger.info(f"Stream server: {self._server_script}")
        decky.logger.info(f"MediaMTX: {self.MEDIAMTX_BIN}")

    @property
    def running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    @property
    def ffmpeg_ok(self) -> bool:
        return self.running

    def start(self, quality: str = "720p30", audio: bool = True, method: str = "mpegts") -> bool:
        if self.running:
            decky.logger.warning("Stream already running")
            return True

        self._method = method
        os.makedirs("/tmp/prysm", exist_ok=True)
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = "/run/user/1000"

        try:
            if method == "webrtc":
                return self._start_webrtc(quality, env)
            else:
                return self._start_mpegts(quality, env)
        except Exception as e:
            decky.logger.error(f"Failed to start: {e}")
            return False

    def _start_mpegts(self, quality: str, env: dict) -> bool:
        if not os.path.isfile(self._server_script):
            decky.logger.error(f"Not found: {self._server_script}")
            return False
        cmd = ["python3", "-u", self._server_script, "--port", str(self.MPEGTS_PORT), "--quality", quality]
        self._proc = subprocess.Popen(cmd, env=env, stdout=open("/tmp/prysm/server.log", "a"),
                                      stderr=subprocess.STDOUT, preexec_fn=os.setsid)
        self._running = True
        decky.logger.info(f"MPEG-TS started (PID {self._proc.pid})")
        time.sleep(3)
        if self._proc.poll() is not None:
            decky.logger.error(f"MPEG-TS died (exit {self._proc.returncode})")
            self._proc = None
            self._running = False
            return False
        return True

    def _start_webrtc(self, quality: str, env: dict) -> bool:
        if not os.path.isfile(self.MEDIAMTX_BIN):
            decky.logger.error(f"MediaMTX not found: {self.MEDIAMTX_BIN}")
            return False
        os.chmod(self.MEDIAMTX_BIN, 0o755)
        presets = {"480p30": (854,480,30,2500), "720p30": (1280,720,30,5000),
                   "720p60": (1280,720,60,8000), "1080p30": (1920,1080,30,8000),
                   "1080p60": (1920,1080,60,12000)}
        w, h, fps, br = presets.get(quality, presets["720p30"])

        # Start MediaMTX
        self._proc = subprocess.Popen(
            [self.MEDIAMTX_BIN, self.MEDIAMTX_CFG],
            stdout=open("/tmp/prysm/mediamtx.log", "a"),
            stderr=subprocess.STDOUT, preexec_fn=os.setsid)
        decky.logger.info(f"MediaMTX started (PID {self._proc.pid})")
        time.sleep(2)
        if self._proc.poll() is not None:
            decky.logger.error("MediaMTX died")
            self._proc = None
            return False

        # Start FFmpeg → RTSP
        self._ffmpeg_proc = subprocess.Popen([
            "ffmpeg", "-y", "-nostdin", "-fflags", "nobuffer", "-flags", "low_delay",
            "-device", "/dev/dri/card0", "-framerate", str(fps), "-f", "kmsgrab", "-i", "-",
            "-vaapi_device", "/dev/dri/renderD128",
            "-vf", f"hwmap=derive_device=vaapi,scale_vaapi=w={w}:h={h}:format=nv12",
            "-c:v", "h264_vaapi", "-b:v", f"{br}k", "-maxrate", f"{br}k",
            "-bufsize", f"{br//2}k", "-g", str(fps), "-bf", "0",
            "-f", "rtsp", "-rtsp_transport", "tcp", "rtsp://127.0.0.1:8554/screen",
        ], env=env, stdout=open("/tmp/prysm/ffmpeg_rtsp.log", "a"),
           stderr=subprocess.STDOUT, preexec_fn=os.setsid)
        decky.logger.info(f"FFmpeg RTSP started (PID {self._ffmpeg_proc.pid})")
        time.sleep(2)
        if self._ffmpeg_proc.poll() is not None:
            decky.logger.error("FFmpeg RTSP died")
            self.stop()
            return False
        self._running = True
        return True

    def check_and_restart(self) -> None:
        """Check if engine died and restart it."""
        if not self._running:
            return
        if self._proc is not None and self._proc.poll() is not None:
            decky.logger.warning(f"Engine exited ({self._proc.returncode}) - restarting")
            self._proc = None
            self._running = False
            # The engine manages FFmpeg restarts internally,
            # so if the engine itself dies, just restart it.
            self.start()

    def stop(self) -> None:
        for proc in [self._ffmpeg_proc, self._proc]:
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
        self._proc = None
        self._ffmpeg_proc = None
        self._running = False
        decky.logger.info("Engine stopped")

    def get_viewer_url(self) -> str:
        ip = self._get_local_ip()
        if self._method == "webrtc":
            return f"http://{ip}:{self.WEBRTC_PORT}/screen/"
        return f"http://{ip}:{self.MPEGTS_PORT}/"

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
    engine: StreamEngine
    vesktop: VesktopManager
    current_mode: StreamMode

    # -- Lifecycle ----------------------------------------------------------

    async def _main(self) -> None:
        decky.logger.info("Prysm starting up")
        self.settings = Settings(decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.discord_ipc = DiscordIPC()
        self.engine = StreamEngine()
        self.vesktop = VesktopManager(self.settings.get("preferred_client", "vesktop"))
        self.current_mode = StreamMode.IDLE
        self._monitor_running = True
        os.makedirs("/tmp/prysm", exist_ok=True)
        decky.logger.info("Prysm ready")

        # Background monitor: auto-restart FFmpeg when Gamescope
        # changes framebuffer format (QAM overlay, notifications, etc.)
        while self._monitor_running:
            await asyncio.sleep(3)
            if self.current_mode == StreamMode.VIEWER:
                self.engine.check_and_restart()

    async def _unload(self) -> None:
        decky.logger.info("Prysm shutting down")
        self._monitor_running = False
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
        viewer_running = self.engine.running
        capture_running = self.engine.ffmpeg_ok

        viewer_url = self.engine.get_viewer_url() if viewer_running else ""

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
            "error": "" if self.discord_ipc.connected else "Discord IPC not available yet - try again in a few seconds",
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
        """Start streaming (MPEG-TS or WebRTC based on settings)."""
        quality = self.settings.get("viewer_quality", "720p30")
        audio = self.settings.get("audio_enabled", True)
        method = self.settings.get("stream_method", "mpegts")

        if not self.engine.start(quality, audio, method):
            return {"success": False, "error": "Failed to start stream engine"}

        self.current_mode = StreamMode.VIEWER
        url = self.engine.get_viewer_url()
        await decky.emit("mode_changed", StreamMode.VIEWER.value)
        await decky.emit("viewer_url", url)

        decky.logger.info(f"Prysm live at {url}")
        return {"success": True, "url": url}

    async def viewer_stop(self) -> dict:
        """Stop WebRTC streaming."""
        self.engine.stop()
        self.current_mode = StreamMode.IDLE
        await decky.emit("mode_changed", StreamMode.IDLE.value)
        return {"success": True}

    async def viewer_get_url(self) -> str:
        return self.engine.get_viewer_url() if self.engine.running else ""

    async def get_stream_stats(self) -> dict:
        """Fetch live stats from the stream server."""
        if not self.engine.running:
            return {"clients": 0, "total_bytes": 0, "ffmpeg_alive": False,
                    "quality": "", "method": "", "uptime": 0}
        try:
            import urllib.request
            port = self.engine.MPEGTS_PORT if self.engine._method == "mpegts" else self.engine.WEBRTC_PORT
            url = f"http://127.0.0.1:{port}/stats"
            if self.engine._method == "webrtc":
                url = f"http://127.0.0.1:9997/v3/paths/list"

            req = urllib.request.urlopen(url, timeout=1)
            import json
            data = json.loads(req.read())

            if self.engine._method == "webrtc":
                # Parse MediaMTX API response
                items = data.get("items", [])
                screen = items[0] if items else {}
                return {
                    "clients": len(screen.get("readers", [])),
                    "total_bytes": screen.get("bytesSent", 0),
                    "ffmpeg_alive": screen.get("ready", False),
                    "quality": self.settings.get("viewer_quality", ""),
                    "method": "webrtc",
                    "uptime": 0,
                }
            else:
                data["quality"] = self.settings.get("viewer_quality", "")
                data["method"] = "mpegts"
                data["uptime"] = 0
                return data
        except Exception:
            return {"clients": 0, "total_bytes": 0, "ffmpeg_alive": self.engine.running,
                    "quality": self.settings.get("viewer_quality", ""),
                    "method": self.engine._method, "uptime": 0}

    # -- Shared controls ----------------------------------------------------

    async def stop_all(self) -> dict:
        """Stop everything."""
        self.engine.stop()
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
