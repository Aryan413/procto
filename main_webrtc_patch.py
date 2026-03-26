"""
main_webrtc_patch.py  —  ExamShield main.py Integration Guide
==============================================================
This file shows EXACTLY where to modify main.py to switch from
MJPEG frame polling to WebRTC streaming.

Apply these three patches to your existing main.py:

PATCH 1: Replace the server import at the top
PATCH 2: Replace start_network_server() call
PATCH 3: Replace the CameraHub.start() call where exam begins
"""

# ═══════════════════════════════════════════════════════════════
# PATCH 1 — Near the top of main.py, replace:
#
#   from server import set_hub, start_server, print_student_url
#
# With:
# ═══════════════════════════════════════════════════════════════

# Import WebRTC server instead of the old MJPEG server
from server_webrtc import register_student, start_server, print_student_url
from webrtc_peer import WebRTCPeer, _AIORTC_AVAILABLE, _SOCKETIO_AVAILABLE

# Global peer reference
_webrtc_peer: WebRTCPeer = None


# ═══════════════════════════════════════════════════════════════
# PATCH 2 — In the if __name__ == "__main__" block, replace:
#
#   start_network_server(port=6000)
#
# With:
# ═══════════════════════════════════════════════════════════════

def start_network_server(port=5050):
    """Drop-in replacement — starts the WebRTC signaling server."""
    start_server()

    # Print WebRTC dependency warnings
    if not _AIORTC_AVAILABLE:
        print("[⚠] 'aiortc' not installed — WebRTC video disabled")
        print("    Fix: pip install aiortc av")
    if not _SOCKETIO_AVAILABLE:
        print("[⚠] 'python-socketio' not installed — WebRTC signaling disabled")
        print("    Fix: pip install 'python-socketio[client]' aiohttp")


# ═══════════════════════════════════════════════════════════════
# PATCH 3 — In ExamWindow.__init__ or wherever the exam starts,
#            find where _hub.start() is called and ADD this after:
#
#   hub.start()
#
# After that line, add:
# ═══════════════════════════════════════════════════════════════

def on_exam_started(hub, server_url: str):
    """
    Call this immediately after hub.start() in the exam window.

    hub:        The CameraHub instance
    server_url: The public server URL (from server_webrtc._public_url)
    """
    global _webrtc_peer

    # Register student with signaling server → get unique token
    token = register_student(hub)
    print_student_url(hub.student_id, token)

    # Start WebRTC peer — streams camera to proctor browser directly
    if _AIORTC_AVAILABLE and _SOCKETIO_AVAILABLE:
        _webrtc_peer = WebRTCPeer(
            server_url=server_url,
            token=token,
            hub=hub,
            student_id=hub.student_id,
            yolo_interval=30,  # Server-side YOLO every 30 frames
        )
        _webrtc_peer.start()
        print(f"[WebRTC] Peer started for {hub.student_id}")
    else:
        print("[WebRTC] Skipped — aiortc or socketio not installed")

    return token


# ═══════════════════════════════════════════════════════════════
# PATCH 4 — In ExamWindow._finish_exam() or wherever the exam ends,
#            add this to stop the WebRTC peer:
# ═══════════════════════════════════════════════════════════════

def on_exam_ended():
    """Call when the exam finishes to clean up WebRTC resources."""
    global _webrtc_peer
    if _webrtc_peer:
        _webrtc_peer.stop()
        _webrtc_peer = None


# ═══════════════════════════════════════════════════════════════
# PATCH 5 — Push violations from CameraHub to proctor via WebRTC
#
# In CameraHub._add_violation() (or wherever violations are logged),
# add this after the existing violation logging:
# ═══════════════════════════════════════════════════════════════

def push_violation(violation_type: str, detail: str = ""):
    """Call from CameraHub when a violation is detected."""
    if _webrtc_peer:
        _webrtc_peer.send_violation(violation_type, detail)


# ═══════════════════════════════════════════════════════════════
# QUICK INTEGRATION EXAMPLE
#
# If you want a minimal test without modifying all of main.py,
# just add these lines right before `MainLogin().run()`:
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test that patches can be imported
    print("ExamShield WebRTC Patch loaded successfully.")
    print(f"  aiortc available:    {_AIORTC_AVAILABLE}")
    print(f"  socketio available:  {_SOCKETIO_AVAILABLE}")
    print()
    print("Patch this file into main.py following the comments above.")
    print("Then run: python main.py")
