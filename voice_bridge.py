"""
voice_bridge.py  —  Low-latency two-way audio for ExamShield
=============================================================
Place this file next to main.py.

Architecture
------------
  Proctor machine runs a WebSocket server on port 6001 (start_voice_bridge).
  Both proctor and student each run a VoiceClient that connects to that server.

  Client roles:
    "proctor"  — captures proctor mic  → sends to bridge  → bridge fans out to students
    "student"  — captures student mic  → sends to bridge  → bridge fans out to proctor

  The bridge simply re-broadcasts each incoming audio chunk to every OTHER
  connected client (i.e. proctor hears student, student hears proctor).

Install deps (if missing):
  pip install sounddevice websocket-client flask-sock
"""

import threading
import queue
import time
import struct

# ── Audio constants ────────────────────────────────────────────────────────────
SAMPLE_RATE  = 16000   # Hz  (16 kHz — good quality, low bandwidth)
CHANNELS     = 1       # mono
CHUNK_FRAMES = 1600    # 100 ms per chunk  (SAMPLE_RATE / 10)
DTYPE        = "int16" # 16-bit PCM

# ── Optional imports ───────────────────────────────────────────────────────────
try:
    import sounddevice as _sd
    _SD_OK = True
except ImportError:
    _SD_OK = False

try:
    import websocket as _ws_lib          # websocket-client
    _WS_CLIENT_OK = True
except ImportError:
    _WS_CLIENT_OK = False

try:
    from flask_sock import Sock as _FlaskSock
    _FLASK_SOCK_OK = True
except ImportError:
    _FLASK_SOCK_OK = False


# ══════════════════════════════════════════════════════════════════════════════
#  BRIDGE SERVER  (runs on proctor machine)
# ══════════════════════════════════════════════════════════════════════════════

_bridge_clients: list = []   # list of (role, ws_conn)
_bridge_lock = threading.Lock()


def start_voice_bridge(port: int = 6001):
    """
    Attach a WebSocket endpoint /ws/voice to a new tiny Flask app on `port`.
    Runs in a daemon thread — safe to call from main thread.
    """
    if not _FLASK_SOCK_OK:
        print("[VoiceBridge] flask-sock not installed — voice disabled")
        print("              Fix: pip install flask-sock")
        return
    if not _SD_OK:
        print("[VoiceBridge] sounddevice not installed — voice disabled")
        print("              Fix: pip install sounddevice")
        return

    try:
        from flask import Flask, request as _freq
        import logging
        app = Flask("VoiceBridgeServer")
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        sock = _FlaskSock(app)

        @sock.route("/ws/voice")
        def _ws_voice(ws):
            role = _freq.args.get("role", "unknown")
            with _bridge_lock:
                _bridge_clients.append((role, ws))
            print(f"[VoiceBridge] {role} connected  (total: {len(_bridge_clients)})")
            try:
                while True:
                    data = ws.receive()
                    if data is None:
                        break
                    # Re-broadcast to all OTHER clients
                    with _bridge_lock:
                        targets = [(r, c) for r, c in _bridge_clients if c is not ws]
                    for _, target_ws in targets:
                        try:
                            target_ws.send(data)
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                with _bridge_lock:
                    _bridge_clients[:] = [(r, c) for r, c in _bridge_clients if c is not ws]
                print(f"[VoiceBridge] {role} disconnected  (total: {len(_bridge_clients)})")

        def _run():
            app.run(host="0.0.0.0", port=port, threaded=True)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        print(f"[VoiceBridge] WebSocket server started on port {port}  (/ws/voice)")

    except Exception as e:
        print(f"[VoiceBridge] Failed to start: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  VOICE CLIENT  (runs on both proctor AND student machine)
# ══════════════════════════════════════════════════════════════════════════════

class VoiceClient:
    """
    Connects to the bridge WebSocket, streams microphone audio out, and
    plays received audio through the speaker — all in background threads.

    Usage:
        vc = VoiceClient(role="student", bridge_url="ws://192.168.1.5:6000/ws/voice")
        vc.start()
        ...
        vc.stop()
    """

    RECONNECT_DELAY = 3.0

    # ── Audio tuning constants ─────────────────────────────────────────────────
    # Boost mic volume sent to the remote side (2.5x = ~8dB louder)
    MIC_GAIN = 2.5
    # Software AEC: subtract this fraction of last-played chunk from mic input
    # to reduce speaker bleed back into the mic (echo).  0=off, 1=full cancel.
    AEC_ATTENUATION = 0.75

    def __init__(self, role: str, bridge_url: str):
        self.role        = role
        self.bridge_url  = bridge_url
        self._running    = False
        self._connected  = False
        self._muted      = False
        self._volume     = 1.0
        self._ws         = None
        self._play_q: queue.Queue = queue.Queue(maxsize=30)
        # Last-played samples for software AEC
        self._last_played     = None   # numpy array or None
        self._last_played_lock = threading.Lock()
        self.on_status_change = None   # optional callback(connected: bool, info: str)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._connect_loop, daemon=True,
                         name=f"VoiceClient-{self.role}").start()
        threading.Thread(target=self._playback_loop, daemon=True,
                         name=f"VoicePlay-{self.role}").start()

    def stop(self):
        self._running   = False
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def toggle_mute(self) -> bool:
        """Toggle mic mute. Returns True if now muted."""
        self._muted = not self._muted
        return self._muted

    def set_volume(self, v: float):
        """Set playback volume multiplier (0.0 – 3.0)."""
        self._volume = max(0.0, float(v))

    # ── Internal — connection loop ─────────────────────────────────────────────

    def _connect_loop(self):
        """Keep reconnecting to the bridge until stop() is called."""
        while self._running:
            url = f"{self.bridge_url}?role={self.role}"
            self._connected = False
            try:
                self._notify_status(False, "Connecting…")
                ws = _ws_lib.WebSocketApp(
                    url,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                self._notify_status(False, str(e))
            finally:
                self._connected = False
            if self._running:
                self._notify_status(False, f"Reconnecting in {self.RECONNECT_DELAY}s…")
                time.sleep(self.RECONNECT_DELAY)

    def _on_open(self, ws):
        self._connected = True
        self._notify_status(True, "Connected")
        threading.Thread(target=self._capture_loop, args=(ws,),
                         daemon=True, name=f"VoiceCap-{self.role}").start()

    def _on_message(self, ws, data):
        """Received audio chunk from the relay — enqueue for playback."""
        if not isinstance(data, (bytes, bytearray)):
            return
        chunk = bytes(data)
        if not chunk:
            return
        # Drop oldest if full to keep latency low
        if self._play_q.full():
            try:
                self._play_q.get_nowait()
            except queue.Empty:
                pass
        try:
            self._play_q.put_nowait(chunk)
        except queue.Full:
            pass

    def _on_error(self, ws, error):
        self._connected = False
        self._notify_status(False, str(error))

    def _on_close(self, ws, *args):
        self._connected = False
        self._notify_status(False, "Disconnected")

    # ── Internal — microphone capture ─────────────────────────────────────────

    def _capture_loop(self, ws):
        """
        Capture mic audio, apply gain + software AEC, send as binary WS frame.

        Software AEC: subtract a scaled copy of the last-played chunk from the
        mic input before sending.  This removes most speaker→mic bleed (echo).

        CRITICAL: WebSocketApp.send() sends TEXT by default.
        Binary audio MUST use opcode=_ws_lib.ABNF.OPCODE_BINARY.
        """
        if not _SD_OK:
            print(f"[VoiceClient/{self.role}] sounddevice missing — capture disabled")
            return

        import numpy as np
        try:
            OPCODE_BINARY = _ws_lib.ABNF.OPCODE_BINARY
        except AttributeError:
            OPCODE_BINARY = 0x2   # fallback for old websocket-client

        print(f"[VoiceClient/{self.role}] Capture started")
        try:
            with _sd.InputStream(
                samplerate = SAMPLE_RATE,
                channels   = CHANNELS,
                dtype      = DTYPE,
                blocksize  = CHUNK_FRAMES,
                latency    = "low",
            ) as stream:
                while self._running and self._connected:
                    try:
                        chunk, overflowed = stream.read(CHUNK_FRAMES)
                    except Exception as e:
                        print(f"[VoiceClient/{self.role}] stream.read error: {e}")
                        break
                    if overflowed:
                        continue   # stale buffer — discard
                    if self._muted:
                        continue

                    samples = chunk[:, 0].astype("float32") if chunk.ndim > 1 else chunk.astype("float32")

                    # ── Software AEC ───────────────────────────────────────────
                    # Subtract attenuated last-played chunk to cancel speaker echo
                    with self._last_played_lock:
                        ref = self._last_played
                    if ref is not None and len(ref) == len(samples):
                        samples -= ref.astype("float32") * self.AEC_ATTENUATION

                    # ── Mic gain boost ─────────────────────────────────────────
                    samples *= self.MIC_GAIN

                    # Clip to int16 range and pack
                    raw = np.clip(samples, -32768, 32767).astype(DTYPE).tobytes()

                    try:
                        ws.send(raw, opcode=OPCODE_BINARY)
                    except Exception as e:
                        print(f"[VoiceClient/{self.role}] send error: {e}")
                        break
        except Exception as e:
            print(f"[VoiceClient/{self.role}] Capture error: {e}")
        print(f"[VoiceClient/{self.role}] Capture stopped")

    # ── Internal — speaker playback ────────────────────────────────────────────

    def _playback_loop(self):
        """Drain the play queue and write PCM chunks to the speaker.
        Stores each played chunk so _capture_loop can subtract it (AEC)."""
        if not _SD_OK:
            return

        import numpy as np

        silence = np.zeros(CHUNK_FRAMES, dtype=DTYPE)
        print(f"[VoiceClient/{self.role}] Playback started")
        try:
            with _sd.OutputStream(
                samplerate = SAMPLE_RATE,
                channels   = CHANNELS,
                dtype      = DTYPE,
                blocksize  = CHUNK_FRAMES,
                latency    = "low",
            ) as stream:
                while self._running:
                    try:
                        data = self._play_q.get(timeout=0.15)
                    except queue.Empty:
                        # Write silence to keep output stream alive
                        with self._last_played_lock:
                            self._last_played = silence
                        stream.write(silence)
                        continue

                    samples = np.frombuffer(data, dtype=DTYPE).copy()
                    # Pad / trim to exact block size
                    if len(samples) < CHUNK_FRAMES:
                        samples = np.pad(samples, (0, CHUNK_FRAMES - len(samples)))
                    elif len(samples) > CHUNK_FRAMES:
                        samples = samples[:CHUNK_FRAMES]

                    if self._volume != 1.0:
                        samples = np.clip(
                            samples.astype("float32") * self._volume,
                            -32768, 32767
                        ).astype(DTYPE)

                    # Record what we played so capture thread can cancel echo
                    with self._last_played_lock:
                        self._last_played = samples.copy()

                    stream.write(samples)
        except Exception as e:
            print(f"[VoiceClient/{self.role}] Playback error: {e}")
        print(f"[VoiceClient/{self.role}] Playback stopped")

    # ── Status helper ──────────────────────────────────────────────────────────

    def _notify_status(self, connected: bool, info: str):
        print(f"[VoiceClient/{self.role}] {'✅' if connected else '⚠'} {info}")
        if callable(self.on_status_change):
            try:
                self.on_status_change(connected, info)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════════════════════════════════════

def make_ws_url(http_url: str, ws_port: int = 6001) -> str:
    """
    Convert an HTTP proctor URL into a WebSocket voice bridge URL.

    For Cloudflare tunnels (trycloudflare.com) the voice relay is served
    on the SAME port as the main Flask app (port 6000 / 443) at /ws/voice.
    Cloudflare does not allow custom ports — appending :6001 breaks the connection.

    For plain LAN/localhost URLs the original port is preserved (port 6000)
    since the relay is also on port 6000 now.

    Examples:
      "https://abc.trycloudflare.com"  →  "wss://abc.trycloudflare.com/ws/voice"
      "http://192.168.1.5:6000"        →  "ws://192.168.1.5:6000/ws/voice"
      "http://127.0.0.1:6000"          →  "ws://127.0.0.1:6000/ws/voice"
    """
    import re
    url = http_url.rstrip("/")
    # Cloudflare tunnel — drop any port, use standard wss (443)
    if "trycloudflare.com" in url or "cloudflare" in url:
        url = re.sub(r":\d+$", "", url)
        if url.startswith("https://"):
            base = "wss://" + url[len("https://"):]
        else:
            base = "wss://" + url[len("http://"):]
        return f"{base}/ws/voice"
    # LAN / localhost — keep existing port, swap scheme only
    if url.startswith("https://"):
        base = "wss://" + url[len("https://"):]
    else:
        base = "ws://" + url[len("http://"):]
    return f"{base}/ws/voice"