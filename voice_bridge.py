"""
╔══════════════════════════════════════════════════════════════════════════════╗
║               ExamShield — Voice Bridge  v2.0                               ║
║          Real-time bidirectional audio via WebSocket                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Server  (proctor machine, port 6001):                                       ║
║    Student  → ws://<proctor-ip>:6001/ws/student  →  relayed to proctor(s)   ║
║    Proctor  → ws://<proctor-ip>:6001/ws/proctor  →  relayed to student(s)   ║
║                                                                              ║
║  Client thread model (fully thread-safe):                                    ║
║    _mic_thread  — InputStream → puts PCM bytes into _tx_queue               ║
║    _net_thread  — WebSocketApp (recv → _rx_queue, send from _tx_queue)      ║
║    _spk_thread  — OutputStream → drains _rx_queue                           ║
║    Sender sub-thread inside on_open drains _tx_queue → ws.send_binary()     ║
║    The mic and speaker threads NEVER touch the WS socket directly.          ║
║                                                                              ║
║  Install:  pip install flask flask-sock sounddevice numpy websocket-client   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import threading
import time
import queue
import numpy as np

# ── Optional imports ─────────────────────────────────────────────────────────
_FLASK_SOCK_AVAILABLE = False
try:
    from flask import Flask
    from flask_sock import Sock
    _FLASK_SOCK_AVAILABLE = True
except ImportError:
    pass

_SOUNDDEVICE_AVAILABLE = False
try:
    import sounddevice as sd
    _SOUNDDEVICE_AVAILABLE = True
except ImportError:
    pass

_WEBSOCKET_AVAILABLE = False
try:
    import websocket          # websocket-client package
    _WEBSOCKET_AVAILABLE = True
except ImportError:
    pass

# ═════════════════════════════════════════════════════════════════════════════
#  Audio constants — MUST match on both ends
# ═════════════════════════════════════════════════════════════════════════════
SAMPLE_RATE  = 16_000
CHANNELS     = 1
DTYPE        = "int16"
CHUNK_MS     = 40                                   # ms per packet
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_MS / 1000)   # 640 samples
GAIN_TX      = 2.0       # mic amplification
JITTER_BUF   = 3         # chunks to pre-buffer before playback starts

# ═════════════════════════════════════════════════════════════════════════════
#  WebSocket Bridge Server  (runs on proctor machine, default port 6001)
# ═════════════════════════════════════════════════════════════════════════════
_student_conns: set = set()
_proctor_conns: set = set()
_conn_lock = threading.Lock()


def _broadcast(targets: set, data: bytes):
    dead = set()
    with _conn_lock:
        snap = set(targets)
    for ws in snap:
        try:
            ws.send(data)
        except Exception:
            dead.add(ws)
    if dead:
        with _conn_lock:
            targets -= dead


def start_voice_bridge(port: int = 6001) -> bool:
    """
    Launch the WebSocket relay server in a daemon thread.
    Returns True on success, False if flask-sock is not installed.
    """
    if not _FLASK_SOCK_AVAILABLE:
        print("[VoiceBridge] flask-sock missing  →  pip install flask-sock")
        return False

    app  = Flask("VoiceBridge")
    sock = Sock(app)

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @sock.route("/ws/student")
    def ws_student(ws):
        with _conn_lock:
            _student_conns.add(ws)
        print(f"[VoiceBridge] Student connected  (total={len(_student_conns)})")
        try:
            while True:
                data = ws.receive()
                if data is None:
                    break
                if isinstance(data, str):
                    data = data.encode()
                _broadcast(_proctor_conns, data)
        except Exception:
            pass
        finally:
            with _conn_lock:
                _student_conns.discard(ws)
            print(f"[VoiceBridge] Student left  (total={len(_student_conns)})")

    @sock.route("/ws/proctor")
    def ws_proctor(ws):
        with _conn_lock:
            _proctor_conns.add(ws)
        print(f"[VoiceBridge] Proctor connected  (total={len(_proctor_conns)})")
        try:
            while True:
                data = ws.receive()
                if data is None:
                    break
                if isinstance(data, str):
                    data = data.encode()
                _broadcast(_student_conns, data)
        except Exception:
            pass
        finally:
            with _conn_lock:
                _proctor_conns.discard(ws)
            print(f"[VoiceBridge] Proctor left  (total={len(_proctor_conns)})")

    from flask import jsonify
    @app.route("/voice_ping")
    def voice_ping():
        with _conn_lock:
            return jsonify(ok=True, students=len(_student_conns),
                           proctors=len(_proctor_conns))

    def _run():
        app.run(host="0.0.0.0", port=port, threaded=True)

    threading.Thread(target=_run, daemon=True, name="VoiceBridgeSrv").start()
    print(f"[VoiceBridge] Started on port {port}")
    print(f"[VoiceBridge]   Student → ws://0.0.0.0:{port}/ws/student")
    print(f"[VoiceBridge]   Proctor → ws://0.0.0.0:{port}/ws/proctor")
    return True


# ═════════════════════════════════════════════════════════════════════════════
#  VoiceClient  — one instance per machine, fully thread-safe
# ═════════════════════════════════════════════════════════════════════════════
class VoiceClient:
    """
    Two-way voice over a persistent WebSocket.

    Parameters
    ----------
    role        : "student" or "proctor"
    bridge_url  : ws:// or wss:// URL of the bridge (port already included),
                  e.g.  "ws://192.168.1.5:6001"
                        "wss://abc123.ngrok-free.app"
    """

    def __init__(self, role: str, bridge_url: str,
                 muted: bool = False, volume: float = 1.0):
        self.role        = role
        self.bridge_url  = bridge_url.rstrip("/")
        self.muted       = muted
        self.volume      = volume
        self._running    = False

        # mic → sender sub-thread → ws.send_binary()
        self._tx_queue   = queue.Queue(maxsize=80)

        # ws.on_message → speaker thread
        self._rx_queue   = queue.Queue(maxsize=80)

        self._mic_thread = None
        self._spk_thread = None
        self._net_thread = None

        self.on_status_change = None   # callback(connected: bool, info: str)

    # ── Public API ────────────────────────────────────────────────────────────
    def start(self):
        if not _SOUNDDEVICE_AVAILABLE:
            print("[VoiceClient] sounddevice missing  →  pip install sounddevice"); return
        if not _WEBSOCKET_AVAILABLE:
            print("[VoiceClient] websocket-client missing  →  pip install websocket-client"); return

        self._running    = True
        self._mic_thread = threading.Thread(target=self._mic_loop,  daemon=True,
                                             name=f"Mic-{self.role}")
        self._spk_thread = threading.Thread(target=self._spk_loop,  daemon=True,
                                             name=f"Spk-{self.role}")
        self._net_thread = threading.Thread(target=self._net_loop,  daemon=True,
                                             name=f"Net-{self.role}")
        self._mic_thread.start()
        self._spk_thread.start()
        self._net_thread.start()
        print(f"[VoiceClient] Started  role={self.role}  url={self.bridge_url}")

    def stop(self):
        self._running = False
        try: self._tx_queue.put_nowait(None)   # unblock sender
        except Exception: pass
        print(f"[VoiceClient] Stopped  role={self.role}")

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        return self.muted

    def set_volume(self, v: float):
        self.volume = max(0.0, min(4.0, float(v)))

    # ── Mic thread: InputStream → _tx_queue ──────────────────────────────────
    def _mic_loop(self):
        while self._running:
            try:
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                    dtype=DTYPE, blocksize=CHUNK_FRAMES) as stream:
                    print(f"[VoiceClient] Mic open  ({self.role})")
                    while self._running:
                        pcm, _ = stream.read(CHUNK_FRAMES)
                        if self.muted:
                            continue
                        amp = np.clip(pcm.astype(np.float32) * GAIN_TX,
                                      -32768, 32767).astype(np.int16)
                        # Drop oldest frame if queue is full (backpressure)
                        if self._tx_queue.full():
                            try: self._tx_queue.get_nowait()
                            except Exception: pass
                        try: self._tx_queue.put_nowait(amp.tobytes())
                        except Exception: pass
            except Exception as e:
                print(f"[VoiceClient] Mic error: {e}  — retry 2s")
                time.sleep(2)

    # ── Speaker thread: _rx_queue → OutputStream ─────────────────────────────
    def _spk_loop(self):
        silence = np.zeros((CHUNK_FRAMES, CHANNELS), dtype=np.int16)
        while self._running:
            try:
                with sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                     dtype=DTYPE, blocksize=CHUNK_FRAMES) as stream:
                    print(f"[VoiceClient] Speaker open  ({self.role})")
                    # Pre-buffer a few chunks before playback begins
                    n = 0
                    while self._running and n < JITTER_BUF:
                        try: self._rx_queue.get(timeout=0.3); n += 1
                        except queue.Empty: pass

                    while self._running:
                        try:
                            raw = self._rx_queue.get(timeout=0.15)
                        except queue.Empty:
                            stream.write(silence)
                            continue

                        arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, CHANNELS)
                        sz  = arr.shape[0]
                        if sz < CHUNK_FRAMES:
                            arr = np.vstack([arr,
                                np.zeros((CHUNK_FRAMES - sz, CHANNELS), dtype=np.int16)])
                        elif sz > CHUNK_FRAMES:
                            arr = arr[:CHUNK_FRAMES]

                        if self.volume != 1.0:
                            arr = np.clip(arr.astype(np.float32) * self.volume,
                                          -32768, 32767).astype(np.int16)
                        stream.write(arr)
            except Exception as e:
                print(f"[VoiceClient] Speaker error: {e}  — retry 2s")
                time.sleep(2)

    # ── Network thread: WebSocketApp + sender sub-thread ─────────────────────
    def _net_loop(self):
        endpoint = f"/ws/{self.role}"
        url      = self.bridge_url + endpoint

        # Normalise scheme
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://"  + url[len("http://"):]

        def on_open(ws):
            self._notify_status(True, f"Voice connected ({self.role})")
            print(f"[VoiceClient] Connected → {url}")

            # Sender sub-thread: drains _tx_queue → ws.send_binary()
            # Exits automatically when this ws session closes
            def _sender():
                while self._running:
                    try:
                        chunk = self._tx_queue.get(timeout=0.3)
                        if chunk is None:
                            break
                        ws.send_binary(chunk)
                    except queue.Empty:
                        continue
                    except Exception as e:
                        print(f"[VoiceClient] Send error: {e}")
                        break

            threading.Thread(target=_sender, daemon=True,
                             name=f"Sender-{self.role}").start()

        def on_message(ws, msg):
            if isinstance(msg, (bytes, bytearray)):
                if self._rx_queue.full():
                    try: self._rx_queue.get_nowait()
                    except Exception: pass
                try: self._rx_queue.put_nowait(bytes(msg))
                except Exception: pass

        def on_error(ws, err):
            print(f"[VoiceClient] WS error: {err}")
            self._notify_status(False, "Reconnecting…")

        def on_close(ws, code, reason):
            print(f"[VoiceClient] WS closed code={code}")
            self._notify_status(False, "Disconnected")

        while self._running:
            try:
                ws_app = websocket.WebSocketApp(
                    url,
                    on_open    = on_open,
                    on_message = on_message,
                    on_error   = on_error,
                    on_close   = on_close,
                )
                ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                print(f"[VoiceClient] WebSocketApp crashed: {e}")
            if self._running:
                print(f"[VoiceClient] Reconnecting in 3s…")
                time.sleep(3)

    def _notify_status(self, connected: bool, info: str):
        if self.on_status_change:
            try: self.on_status_change(connected, info)
            except Exception: pass


# ═════════════════════════════════════════════════════════════════════════════
#  URL helper
# ═════════════════════════════════════════════════════════════════════════════
def make_ws_url(http_base_url: str, ws_port: int = 6001) -> str:
    """
    Build the WebSocket URL for the voice bridge from an HTTP base URL.

    LAN:    "http://192.168.1.5:6000"       →  "ws://192.168.1.5:6001"
    ngrok:  "https://abc.ngrok-free.app"    →  "wss://abc.ngrok-free.app"
            (ngrok tunnels all ports on the same hostname, no :6001 needed)
    """
    import urllib.parse
    p = urllib.parse.urlparse(http_base_url)
    host = p.hostname or "127.0.0.1"

    # Detect ngrok domains — they serve everything on port 443
    ngrok_domains = (".ngrok.io", ".ngrok-free.app", ".ngrok.app", ".ngrok.dev")
    is_ngrok = any(host.endswith(d) for d in ngrok_domains)

    if is_ngrok or p.scheme in ("https", "wss"):
        # ngrok: no extra port, wss://
        return f"wss://{host}"
    else:
        return f"ws://{host}:{ws_port}"


# ═════════════════════════════════════════════════════════════════════════════
#  Stand-alone test
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Starting VoiceBridge standalone…")
    if not start_voice_bridge(6001):
        raise SystemExit(1)
    print("Listening. Open two terminals and test with a WebSocket client.")
    print("  Student: ws://localhost:6001/ws/student")
    print("  Proctor: ws://localhost:6001/ws/proctor")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("Stopped.")