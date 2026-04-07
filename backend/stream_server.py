#!/usr/bin/env python3
"""
Prysm Stream Server — kmsgrab → VAAPI H.264 → MPEG-TS → HTTP → mpegts.js

Dead simple. No WebRTC. No PipeWire portal. No Rust.
Just FFmpeg piping MPEG-TS to an HTTP server.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

PORT = 7770
QUALITY = "720p30"

PRESETS = {
    "480p30":  (854, 480, 30, 2500),
    "720p30":  (1280, 720, 30, 5000),
    "720p60":  (1280, 720, 60, 8000),
    "1080p30": (1920, 1080, 30, 8000),
    "1080p60": (1920, 1080, 60, 12000),
}

VIEWER_HTML = r"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prysm</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a12;color:#e8e8f0;font-family:system-ui;overflow:hidden;height:100vh}
video{width:100vw;height:100vh;object-fit:contain;background:#000}
.bar{position:fixed;top:0;left:0;right:0;padding:10px 16px;background:linear-gradient(to bottom,rgba(0,0,0,.8),transparent);display:flex;justify-content:space-between;align-items:center;z-index:10;opacity:0;transition:opacity .3s}
body:hover .bar{opacity:1}
.logo{font-weight:800;font-size:1rem;background:linear-gradient(135deg,#a855f7,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.pill{font-size:.78rem;padding:4px 10px;border-radius:12px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08)}
.d{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px;animation:p 2s infinite}
.d.on{background:#22c55e;box-shadow:0 0 6px rgba(34,197,94,.6)}
.d.off{background:#ef4444;box-shadow:0 0 6px rgba(239,68,68,.4);animation:none}
@keyframes p{0%,100%{opacity:1}50%{opacity:.5}}
.btns{position:fixed;bottom:0;left:0;right:0;display:flex;justify-content:center;gap:10px;padding:14px;background:linear-gradient(to top,rgba(0,0,0,.8),transparent);opacity:0;transition:opacity .3s;z-index:10}
body:hover .btns{opacity:1}
.btn{padding:8px 16px;border:1px solid rgba(255,255,255,.1);border-radius:8px;background:rgba(255,255,255,.07);color:#e8e8f0;font-size:.82rem;font-weight:600;cursor:pointer}
.btn:hover{background:rgba(255,255,255,.12)}
.dbg{position:fixed;bottom:50px;right:12px;z-index:20;font-size:.7rem;font-family:'SF Mono',monospace;color:rgba(255,255,255,.5);text-align:right;pointer-events:none;line-height:1.5}
.dbg.show{color:rgba(255,255,255,.85);background:rgba(0,0,0,.6);padding:8px 10px;border-radius:8px;border:1px solid rgba(255,255,255,.08);pointer-events:auto}
.dbg-toggle{position:fixed;bottom:14px;right:12px;z-index:20;padding:6px 12px;border:1px solid rgba(255,255,255,.1);border-radius:8px;background:rgba(255,255,255,.05);color:rgba(255,255,255,.4);font-size:.7rem;cursor:pointer}
.dbg-toggle:hover{color:rgba(255,255,255,.7);background:rgba(255,255,255,.1)}
</style>
</head><body>
<div class="bar">
<span class="logo">PRYSM</span>
<span class="pill"><span class="d off" id="dot"></span><span id="st">Connecting...</span></span>
</div>
<video id="v" autoplay muted playsinline></video>
<div class="btns">
<button class="btn" onclick="v.muted=!v.muted;this.textContent=v.muted?'Unmute':'Mute'">Unmute</button>
<button class="btn" onclick="document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen()">Fullscreen</button>
</div>
<div class="dbg" id="dbg"></div>
<button class="dbg-toggle" onclick="toggleDbg()">Stats</button>
<script src="https://cdn.jsdelivr.net/npm/mpegts.js@1.8.0/dist/mpegts.js"></script>
<script>
const v=document.getElementById('v'),st=document.getElementById('st'),dot=document.getElementById('dot'),dbg=document.getElementById('dbg');
let dbgOn=false,player=null,startTime=Date.now(),bytesRecv=0;

function toggleDbg(){dbgOn=!dbgOn;dbg.className=dbgOn?'dbg show':'dbg'}

function fmt(n){return n>1e6?(n/1e6).toFixed(1)+'MB':n>1e3?(n/1e3).toFixed(0)+'KB':n+'B'}

function updateStats(){
if(!dbgOn)return;
let lines=[];
const up=((Date.now()-startTime)/1000).toFixed(0);
lines.push('uptime: '+up+'s');

// Server stats
fetch(location.origin+'/stats').then(r=>r.json()).then(s=>{
lines.push('server: '+(s.ffmpeg_alive?'capturing':'FFmpeg dead'));
lines.push('clients: '+s.clients);
lines.push('total: '+fmt(s.total_bytes));

// Player stats
if(player&&player.statisticsInfo){
const si=player.statisticsInfo;
lines.push('---');
lines.push('speed: '+(si.speed||0).toFixed(0)+' KB/s');
lines.push('dropped: '+(si.droppedFrames||0));
lines.push('decoded: '+(si.decodedFrames||0));
if(v.buffered&&v.buffered.length>0){
const buf=v.buffered.end(v.buffered.length-1)-v.currentTime;
lines.push('buffer: '+buf.toFixed(2)+'s');
}
if(v.videoWidth)lines.push('res: '+v.videoWidth+'x'+v.videoHeight);
}
dbg.innerHTML=lines.join('<br>');
}).catch(()=>{dbg.innerHTML='stats error'});
}

setInterval(updateStats,1000);

function go(){
if(!mpegts.getFeatureList().mseLivePlayback){st.textContent='MSE not supported';return}
player=mpegts.createPlayer({type:'mpegts',isLive:true,url:location.origin+'/stream'},{
enableWorker:true,liveBufferLatencyChasing:true,
liveBufferLatencyMaxLatency:1.5,liveBufferLatencyMinRemain:0.5,
liveSync:true,liveSyncMaxLatency:1.2,liveSyncTargetLatency:0.8,
liveSyncPlaybackRate:1.08,enableStashBuffer:false,stashInitialSize:384,
autoCleanupSourceBuffer:true,autoCleanupMaxBackwardDuration:1,
autoCleanupMinBackwardDuration:0.5});
player.attachMediaElement(v);player.load();
v.addEventListener('canplay',()=>{v.play();st.textContent='Live';dot.className='d on'});
player.on(mpegts.Events.ERROR,(e,d)=>{
st.textContent='Reconnecting...';dot.className='d off';
console.error('mpegts error:',e,d);
setTimeout(()=>{player.unload();player.load()},2000)});
player.on(mpegts.Events.STATISTICS_INFO,(s)=>{bytesRecv=s.totalBytes||0});
}
go();
v.addEventListener('click',()=>{v.muted=!v.muted});
</script></body></html>"""

# ── Globals ──
ffmpeg_proc = None
clients = []
clients_lock = threading.Lock()
total_bytes = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/stream":
            self.serve_stream()
        elif self.path == "/stats":
            self.serve_stats()
        else:
            self.serve_viewer()

    def serve_viewer(self):
        body = VIEWER_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = []
        with clients_lock:
            clients.append(q)
        print(f"[http] +client ({len(clients)} total)")

        synced = False
        try:
            while True:
                if q:
                    chunk = q.pop(0)
                    # Sync to TS packet boundary on first chunk
                    if not synced:
                        idx = chunk.find(b'\x47')
                        if idx >= 0:
                            chunk = chunk[idx:]
                            synced = True
                        else:
                            continue
                    self.wfile.write(chunk)
                    self.wfile.flush()
                else:
                    time.sleep(0.002)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with clients_lock:
                if q in clients:
                    clients.remove(q)
            print(f"[http] -client ({len(clients)} total)")

    def serve_stats(self):
        import json
        body = json.dumps({
            "clients": len(clients),
            "total_bytes": total_bytes,
            "ffmpeg_alive": ffmpeg_proc is not None and ffmpeg_proc.poll() is None,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_ffmpeg(quality):
    w, h, fps, br = PRESETS.get(quality, PRESETS["720p30"])
    cmd = [
        "ffmpeg", "-y",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-device", "/dev/dri/card0",
        "-framerate", str(fps),
        "-f", "kmsgrab", "-i", "-",
        "-vaapi_device", "/dev/dri/renderD128",
        "-vf", f"hwmap=derive_device=vaapi,scale_vaapi=w={w}:h={h}:format=nv12",
        "-c:v", "h264_vaapi",
        "-b:v", f"{br}k", "-maxrate", f"{br}k",
        "-bufsize", f"{br // 2}k",
        "-g", str(fps), "-bf", "0",
        "-f", "mpegts",
        "-muxdelay", "0", "-muxpreload", "0",
        "-flush_packets", "1",
        "pipe:1",
    ]
    print(f"[ffmpeg] {' '.join(cmd)}")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "XDG_RUNTIME_DIR": "/run/user/1000"},
    )


def broadcast_loop():
    global ffmpeg_proc, total_bytes
    restart_count = 0

    while True:
        ffmpeg_proc = start_ffmpeg(QUALITY)
        print(f"[ffmpeg] Started PID={ffmpeg_proc.pid}")

        while True:
            chunk = ffmpeg_proc.stdout.read(188 * 32)  # 6016 bytes = 32 TS packets
            if not chunk:
                break
            total_bytes += len(chunk)
            with clients_lock:
                for q in clients:
                    q.append(chunk)
                    # Keep queue tight — drop old data aggressively
                    while len(q) > 100:
                        q.pop(0)

        rc = ffmpeg_proc.wait()
        restart_count += 1
        print(f"[ffmpeg] Exited ({rc}), restart #{restart_count}")
        time.sleep(0.5)


def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "localhost"


def main():
    global PORT, QUALITY
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--port" and i < len(sys.argv) - 1:
            PORT = int(sys.argv[i + 1])
        if a == "--quality" and i < len(sys.argv) - 1:
            QUALITY = sys.argv[i + 1]

    ip = get_ip()
    print(f"[prysm] ▲ Starting on :{PORT} quality={QUALITY}")
    print(f"[prysm] Viewer: http://{ip}:{PORT}/")
    print(f"[prysm] Stream: http://{ip}:{PORT}/stream")

    # Start broadcast in background
    threading.Thread(target=broadcast_loop, daemon=True).start()

    # HTTP server (blocks)
    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if ffmpeg_proc:
            ffmpeg_proc.terminate()
        server.shutdown()


if __name__ == "__main__":
    main()
