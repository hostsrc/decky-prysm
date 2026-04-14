#!/usr/bin/env python3
"""
Prysm Capture Daemon — PipeWire portal capture + VAAPI encode + HTTP stream.

Flow:
  1. xdg-desktop-portal-gamescope → create screencast session
  2. OpenPipeWireRemote → get PipeWire FD
  3. GStreamer pipewiresrc (fd=FD, path=NODE) → raw video
  4. Pipe to FFmpeg → h264_vaapi → MPEG-TS
  5. Serve MPEG-TS via HTTP chunked transfer
  6. Browser uses mpegts.js for low-latency playback

Usage:
  python3 capture_daemon.py [--port 7770] [--quality 720p30]
"""

import asyncio
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── Portal ScreenCast ──────────────────────────────────────────────

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


def create_screencast_session():
    """Create a portal screencast session and return (node_id, pw_fd)."""
    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    bus = dbus.SessionBus()

    portal = bus.get_object(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
    )
    sc = dbus.Interface(portal, "org.freedesktop.portal.ScreenCast")

    result = {"session": None, "node_id": None, "pw_fd": None, "step": 0}

    def on_response(response, results):
        result["step"] += 1
        step = result["step"]

        if step == 1:
            # CreateSession response
            result["session"] = str(results.get("session_handle", ""))
            print(f"[portal] Session: {result['session']}")
            sc.SelectSources(
                dbus.ObjectPath(result["session"]),
                dbus.Dictionary(
                    {"handle_token": dbus.String("sel"), "types": dbus.UInt32(1)},
                    signature="sv",
                ),
            )

        elif step == 2:
            # SelectSources response
            print("[portal] Sources selected")
            sc.Start(
                dbus.ObjectPath(result["session"]),
                "",
                dbus.Dictionary(
                    {"handle_token": dbus.String("start")},
                    signature="sv",
                ),
            )

        elif step == 3:
            # Start response
            streams = results.get("streams", [])
            if not streams:
                print("[portal] ERROR: No streams returned")
                loop.quit()
                return

            result["node_id"] = int(streams[0][0])
            print(f"[portal] Node ID: {result['node_id']}")

            # Get the PipeWire file descriptor
            fd_obj = sc.OpenPipeWireRemote(
                dbus.ObjectPath(result["session"]),
                dbus.Dictionary({}, signature="sv"),
            )
            # dbus.UnixFd wraps the fd — take_fd() extracts and owns it
            result["pw_fd"] = fd_obj.take()
            print(f"[portal] PipeWire FD: {result['pw_fd']}")
            loop.quit()

    bus.add_signal_receiver(
        on_response,
        signal_name="Response",
        dbus_interface="org.freedesktop.portal.Request",
    )

    sc.CreateSession(
        dbus.Dictionary(
            {
                "handle_token": dbus.String("cs"),
                "session_handle_token": dbus.String("sess"),
            },
            signature="sv",
        )
    )

    # Timeout after 10s
    GLib.timeout_add_seconds(10, loop.quit)
    loop.run()

    if result["node_id"] is None or result["pw_fd"] is None:
        raise RuntimeError("Failed to create screencast session")

    return result["node_id"], result["pw_fd"]


# ─── Capture Pipeline ───────────────────────────────────────────────

QUALITY_PRESETS = {
    "480p30":  (854, 480, 30, 2500),
    "720p30":  (1280, 720, 30, 5000),
    "720p60":  (1280, 720, 60, 8000),
    "1080p30": (1920, 1080, 30, 8000),
    "1080p60": (1920, 1080, 60, 12000),
}


def start_capture_pipeline(node_id: int, pw_fd: int, quality: str = "720p30"):
    """
    GStreamer captures from PipeWire (in-process, so FD survives).
    Pipes raw video to FFmpeg for VAAPI H.264 encoding.
    FFmpeg outputs MPEG-TS to stdout.

    Returns the FFmpeg subprocess (read stdout for MPEG-TS data).
    """
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)

    w, h, fps, bitrate = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["720p30"])

    # Create a pipe: GStreamer writes → FFmpeg reads
    read_fd, write_fd = os.pipe()

    # GStreamer pipeline using the portal PipeWire FD (in-process!)
    pipeline_str = (
        f"pipewiresrc fd={pw_fd} path={node_id} do-timestamp=true ! "
        f"videoconvert ! "
        f"video/x-raw,format=NV12,width={w},height={h} ! "
        f"fdsink fd={write_fd}"
    )
    print(f"[capture] GStreamer: {pipeline_str}")
    pipeline = Gst.parse_launch(pipeline_str)

    # Start GStreamer
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        # Try without format conversion
        print("[capture] Trying without format constraints...")
        pipeline_str = (
            f"pipewiresrc fd={pw_fd} path={node_id} do-timestamp=true ! "
            f"videoconvert ! video/x-raw,format=NV12 ! "
            f"videoscale ! video/x-raw,width={w},height={h} ! "
            f"fdsink fd={write_fd}"
        )
        pipeline = Gst.parse_launch(pipeline_str)
        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer pipeline failed to start")

    print(f"[capture] GStreamer pipeline PLAYING")

    # FFmpeg: reads raw NV12 from pipe → VAAPI H.264 → MPEG-TS
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-f", "rawvideo",
        "-pix_fmt", "nv12",
        "-s", f"{w}x{h}",
        "-r", str(fps),
        "-i", f"pipe:{read_fd}",
        "-vaapi_device", "/dev/dri/renderD128",
        "-vf", "format=nv12,hwupload",
        "-c:v", "h264_vaapi",
        "-b:v", f"{bitrate}k",
        "-maxrate", f"{bitrate}k",
        "-bufsize", f"{bitrate // 2}k",
        "-g", str(fps),
        "-bf", "0",
        "-f", "mpegts",
        "-muxdelay", "0",
        "-muxpreload", "0",
        "-flush_packets", "1",
        "pipe:1",
    ]
    print(f"[capture] FFmpeg: {' '.join(ffmpeg_cmd)}")

    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(read_fd,),
    )

    # Close write_fd in parent won't work since GStreamer uses it
    # Close read_fd in parent since FFmpeg owns it now
    os.close(read_fd)

    # Monitor FFmpeg stderr in background
    def log_ffmpeg_stderr():
        for line in ffmpeg_proc.stderr:
            print(f"[ffmpeg] {line.decode().rstrip()}")
    threading.Thread(target=log_ffmpeg_stderr, daemon=True).start()

    # Monitor GStreamer bus in background
    def gst_bus_monitor():
        bus = pipeline.get_bus()
        while True:
            msg = bus.timed_pop_filtered(
                Gst.CLOCK_TIME_NONE,
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED,
            )
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                print(f"[gstreamer] ERROR: {err.message}")
                print(f"[gstreamer] Debug: {debug}")
                break
            elif msg.type == Gst.MessageType.EOS:
                print("[gstreamer] End of stream")
                break
    threading.Thread(target=gst_bus_monitor, daemon=True).start()

    print(f"[capture] FFmpeg PID={ffmpeg_proc.pid}")
    return pipeline, ffmpeg_proc


# ─── HTTP Server (chunked MPEG-TS) ──────────────────────────────────

VIEWER_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prysm</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a12;color:#e8e8f0;font-family:system-ui;overflow:hidden;height:100vh}
#v{width:100vw;height:100vh;object-fit:contain;background:#000}
.bar{position:fixed;top:0;left:0;right:0;padding:10px 16px;background:linear-gradient(to bottom,rgba(0,0,0,.8),transparent);display:flex;justify-content:space-between;align-items:center;z-index:10;opacity:0;transition:opacity .3s}
body:hover .bar{opacity:1}
.logo{font-weight:800;font-size:1rem;background:linear-gradient(135deg,#a855f7,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.pill{font-size:.78rem;padding:4px 10px;border-radius:12px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08)}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#22c55e;margin-right:6px;box-shadow:0 0 6px rgba(34,197,94,.6)}
</style>
</head><body>
<div class="bar">
<span class="logo">PRYSM</span>
<span class="pill"><span class="dot" id="dot"></span><span id="st">Connecting...</span></span>
</div>
<video id="v" autoplay muted playsinline></video>
<script src="https://cdn.jsdelivr.net/npm/mpegts.js@1.8.0/dist/mpegts.js"></script>
<script>
const v=document.getElementById('v'),st=document.getElementById('st'),dot=document.getElementById('dot');
if(mpegts.getFeatureList().mseLivePlayback){
const p=mpegts.createPlayer({type:'mpegts',isLive:true,url:'/stream'},{
enableWorker:true,liveBufferLatencyChasing:true,
liveBufferLatencyMaxLatency:1.0,liveBufferLatencyMinRemain:0.2,
liveSync:true,liveSyncMaxLatency:0.8,liveSyncTargetLatency:0.3,
liveSyncPlaybackRate:1.2,enableStashBuffer:false,stashInitialSize:128});
p.attachMediaElement(v);p.load();
v.addEventListener('canplay',()=>{v.play();st.textContent='Live';});
p.on(mpegts.Events.ERROR,()=>{st.textContent='Reconnecting...';dot.style.background='#ef4444';
setTimeout(()=>{p.unload();p.load();},2000);});
}
v.addEventListener('click',()=>{v.muted=!v.muted});
</script></body></html>"""

# Shared buffer for broadcasting MPEG-TS chunks
clients = []
clients_lock = threading.Lock()


class StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            q = []
            with clients_lock:
                clients.append(q)
            print(f"[http] Client connected ({len(clients)} total)")

            try:
                while True:
                    if q:
                        chunk = q.pop(0)
                        # Chunked encoding
                        self.wfile.write(f"{len(chunk):x}\r\n".encode())
                        self.wfile.write(chunk)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    else:
                        time.sleep(0.001)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with clients_lock:
                    if q in clients:
                        clients.remove(q)
                print(f"[http] Client disconnected ({len(clients)} total)")

        elif self.path == "/stats":
            import json
            stats = {"clients": len(clients), "status": "running"}
            body = json.dumps(stats).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        else:
            body = VIEWER_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def broadcast_loop(ffmpeg_proc):
    """Read MPEG-TS from FFmpeg stdout and broadcast to all HTTP clients."""
    total = 0
    while True:
        chunk = ffmpeg_proc.stdout.read(4096)
        if not chunk:
            print("[broadcast] FFmpeg stdout closed")
            break
        total += len(chunk)
        with clients_lock:
            for q in clients:
                q.append(chunk)
                # Prevent unbounded growth
                while len(q) > 200:
                    q.pop(0)
        if total < 50000 or total % 500000 < 4096:
            print(f"[broadcast] {total} bytes, {len(clients)} clients")


# ─── Main ────────────────────────────────────────────────────────────

def main():
    port = 7770
    quality = "720p30"

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])
        if arg == "--quality" and i < len(sys.argv) - 1:
            quality = sys.argv[i + 1]

    print(f"[prysm] Starting capture daemon — port={port} quality={quality}")

    # Step 1: Create portal screencast session
    print("[prysm] Creating screencast session...")
    node_id, pw_fd = create_screencast_session()
    print(f"[prysm] Got PipeWire node={node_id} fd={pw_fd}")

    # Step 2: Start capture pipeline
    print("[prysm] Starting capture pipeline...")
    gst_pipeline, ffmpeg_proc = start_capture_pipeline(node_id, pw_fd, quality)

    # Step 3: Start HTTP server
    print(f"[prysm] Starting HTTP server on :{port}")
    server = HTTPServer(("0.0.0.0", port), StreamHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    ip = get_local_ip()
    print(f"[prysm] ===================================")
    print(f"[prysm] Viewer: http://{ip}:{port}/")
    print(f"[prysm] Stream: http://{ip}:{port}/stream")
    print(f"[prysm] Stats:  http://{ip}:{port}/stats")
    print(f"[prysm] ===================================")

    # Step 4: Broadcast FFmpeg output to HTTP clients
    try:
        broadcast_loop(ffmpeg_proc)
    except KeyboardInterrupt:
        print("[prysm] Shutting down...")
    finally:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        gst_pipeline.set_state(Gst.State.NULL)
        ffmpeg_proc.terminate()
        server.shutdown()


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


if __name__ == "__main__":
    main()
