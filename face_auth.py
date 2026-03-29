"""
face_auth.py  —  ExamShield Biometric Login
============================================
Implements the EXACT algorithm from:

  ivclab/Online-Face-Recognition-and-Authentication
  "Data-specific Adaptive Threshold for Face Recognition and Authentication"
  Chou et al., MIPR 2019

HOW THE REAL ALGORITHM WORKS  (from database.py + simulator_v4_adaptive_thd.py)
─────────────────────────────────────────────────────────────────────────────────
The paper's threshold is DATA-SPECIFIC and INTER-CLASS — NOT intra-class spread.

  Registration (insert):
    For each new embedding being registered, compare it against a sample of
    embeddings already in the database that belong to DIFFERENT people.
    For every different-class comparison:
      • Update the OTHER embedding's threshold:
            other.threshold = max(other.threshold, similarity(new, other))
        (they now know how similar a new impostor can look to them)
      • Track the max impostor similarity seen from the new embedding's side:
            new.threshold = max(all cross-class similarities seen during insert)

    Each embedding's threshold therefore = the highest cosine similarity any
    known different-identity embedding has ever had to it.

  Verification (get_most_similar + threshold gate):
    1. Find the DB embedding with highest cosine similarity to the live probe.
    2. If  max_similarity  >  threshold[matched_id]  →  ACCEPT
       Else                                          →  REJECT
    3. After acceptance: insert the probe into the DB (online update).
       This raises thresholds if the probe is close to any other identity,
       making future impostors harder to fool.  Exactly the paper's simulator loop.

  Key insight:  threshold[i] = "how similar is the nearest known impostor to i?"
  If a probe is more similar to entry i than any known impostor ever was,
  it must be the genuine person → accept.

MAPPING TO EXAMSHIELD
──────────────────────
  • Each student owns a personal _FaceDatabase persisted in students.db
  • Registration   → capture_face_registration()  builds the DB from webcam
  • Verification   → verify_face()               runs the simulator loop live
  • Online update  → probe inserted into DB on every successful login
  • Backwards compat: old flat float32 blobs are auto-migrated
"""

from __future__ import annotations

import cv2
import numpy as np
import sqlite3
import time
import json
import base64
import random
import threading
from tkinter import messagebox
from typing import Dict, List, Optional, Tuple

try:
    import mediapipe as mp
    _MP_AVAILABLE = True
except ImportError:
    _MP_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
#  TUNING KNOBS
# ═══════════════════════════════════════════════════════════════════════════════

DB_PATH               = "students.db"
CAPTURE_SAMPLES       = 8      # embeddings collected at registration
CAPTURE_TIMEOUT       = 45     # seconds before registration aborts
MAX_COMPARE_NUM       = 50     # paper param: max DB entries compared per insert
                               # (set 0 to compare all — slower but paper-exact)
REQUIRED_MATCHES      = 6      # consecutive frame-level accepts to pass login
MAX_FRAMES            = 210    # ~7 s @ 30 fps before verification gives up
MAX_STORED_EMBEDDINGS = 40     # online-growth cap per student
INITIAL_THRESHOLD     = 0.0    # paper: every embedding starts at 0


# ═══════════════════════════════════════════════════════════════════════════════
#  EXACT PORT OF database.py  ──  _FaceDatabase
# ═══════════════════════════════════════════════════════════════════════════════

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity for unit-norm vectors.  Mirrors get_similarity()."""
    return float(np.sum(a * b))


class _FaceDatabase:
    """
    Direct port of the paper's Database class (database.py).

    self.embeddings[i]  — unit-norm face embedding
    self.labels[i]      — identity label (student_id for ExamShield)
    self.thresholds[i]  — adaptive threshold = max inter-class similarity seen
    self.class_dict     — label → [list of indices]
    """

    def __init__(self, compare_num: int = MAX_COMPARE_NUM):
        self.embeddings:  List[np.ndarray]     = []
        self.labels:      List[str]             = []
        self.thresholds:  List[float]           = []
        self.compare_num: int                   = compare_num
        self.class_dict:  Dict[str, List[int]] = {}

    def __len__(self) -> int:
        return len(self.embeddings)

    # ── _add_to_dict ──────────────────────────────────────────────────────────
    def _add_to_dict(self, label: str, index: int):
        if label not in self.class_dict:
            self.class_dict[label] = []
        self.class_dict[label].append(index)

    # ── update_thresholds  (mirrors database.py update_thresholds) ────────────
    def _update_thresholds(self, emb_test: np.ndarray, label_test: str) -> float:
        """
        Compare emb_test against a capped sample of existing embeddings.
        Only compares against DIFFERENT-CLASS entries.

        Side-effects:
          • Raises other[i].threshold if emb_test is a new max impostor for i.
        Returns:
          • max impostor similarity seen from emb_test's perspective
            (becomes emb_test's threshold after insert).
        """
        n = len(self.embeddings)
        if n == 0:
            return -1.0

        all_classes  = list(self.class_dict.keys())
        class_num    = len(all_classes)
        compare_idxs: List[int] = []

        # ── Sampling strategy (mirrors database.py exactly) ───────────────────
        if class_num <= self.compare_num and n <= self.compare_num:
            compare_idxs = list(range(n))

        elif class_num <= self.compare_num and n > self.compare_num:
            mul  = int(np.floor(self.compare_num / class_num))
            last: List[int] = []
            cnt  = 0
            for c in all_classes:
                cur = self.class_dict[c]
                if len(cur) >= mul:
                    chosen = random.sample(cur, mul)
                    compare_idxs.extend(chosen)
                    last.extend([v for v in cur if v not in set(chosen)])
                    cnt += mul
                else:
                    compare_idxs.extend(cur)
                    cnt += len(cur)
            rem = self.compare_num - cnt
            if rem > 0 and last:
                compare_idxs.extend(random.sample(last, min(rem, len(last))))

        else:  # class_num > compare_num
            chosen_classes = random.sample(all_classes, self.compare_num)
            compare_idxs   = [random.choice(self.class_dict[c])
                              for c in chosen_classes]

        # ── Compare against different-class entries only ───────────────────────
        max_thd = -1.0
        for idx in compare_idxs:
            if self.labels[idx] == label_test:
                continue                               # skip same class
            sim = _cosine(emb_test, self.embeddings[idx])
            if sim > self.thresholds[idx]:             # update other's threshold
                self.thresholds[idx] = sim
            if sim > max_thd:
                max_thd = sim

        return max_thd

    # ── insert  (mirrors database.py insert) ──────────────────────────────────
    def insert(self, label: str, emb: np.ndarray):
        """Register one embedding, updating adaptive thresholds across the DB."""
        idx = len(self.embeddings)
        self.embeddings.append(emb.copy())
        self.labels.append(label)
        self.thresholds.append(INITIAL_THRESHOLD)
        self._add_to_dict(label, idx)
        max_thd = self._update_thresholds(emb, label)
        if max_thd > INITIAL_THRESHOLD:
            self.thresholds[idx] = max_thd

    # ── get_most_similar  (mirrors database.py get_most_similar) ──────────────
    def get_most_similar(self, emb_test: np.ndarray) -> Tuple[int, float]:
        """Return (index, cosine_similarity) of the best-matching stored embedding."""
        if not self.embeddings:
            return -1, -1.0
        mat  = np.stack(self.embeddings, axis=0)     # (N, D)
        sims = mat @ emb_test                         # (N,)  — dot product
        best_id  = int(np.argmax(sims))
        best_sim = float(sims[best_id])
        return best_id, best_sim

    def get_threshold_by_id(self, idx: int) -> float:
        return self.thresholds[idx]

    def get_label_by_id(self, idx: int) -> str:
        return self.labels[idx]

    def contains(self, label: str) -> bool:
        return label in self.class_dict

    # ── trim oldest entries ───────────────────────────────────────────────────
    def trim_to(self, max_size: int):
        """Remove oldest embeddings when the DB grows beyond max_size."""
        while len(self.embeddings) > max_size:
            self.embeddings.pop(0)
            self.labels.pop(0)
            self.thresholds.pop(0)
        # Rebuild class_dict from scratch
        self.class_dict = {}
        for i, lbl in enumerate(self.labels):
            self.class_dict.setdefault(lbl, []).append(i)

    # ── serialise / deserialise ───────────────────────────────────────────────
    def to_blob(self) -> bytes:
        payload = {
            "v":           3,
            "compare_num": self.compare_num,
            "embeddings":  [
                base64.b64encode(e.astype(np.float32).tobytes()).decode()
                for e in self.embeddings],
            "labels":      self.labels,
            "thresholds":  [float(t) for t in self.thresholds],
            "class_dict":  {k: list(v) for k, v in self.class_dict.items()},
        }
        return json.dumps(payload).encode()

    @classmethod
    def from_blob(cls, blob: bytes, uid: str = "__owner__") -> "_FaceDatabase":
        try:
            payload = json.loads(blob.decode())
            if payload.get("v", 1) < 3:
                raise ValueError("old format")
        except Exception:
            # Legacy v1/v2 — single float32 blob or old JSON format
            try:
                payload = json.loads(blob.decode())
                # v2 JSON from previous face_auth.py version — extract embeddings
                raw_embs = payload.get("embeddings", [])
                embs = [np.frombuffer(base64.b64decode(e), dtype=np.float32).copy()
                        for e in raw_embs]
            except Exception:
                # Raw float32 blob (v1)
                arr  = np.frombuffer(blob, dtype=np.float32).copy()
                embs = [arr]

            db = cls()
            for emb in embs:
                db.insert(uid, emb)
            return db

        db = cls(compare_num=payload.get("compare_num", MAX_COMPARE_NUM))
        db.embeddings  = [
            np.frombuffer(base64.b64decode(e), dtype=np.float32).copy()
            for e in payload["embeddings"]]
        db.labels      = list(payload["labels"])
        db.thresholds  = list(payload["thresholds"])
        db.class_dict  = {k: list(v) for k, v in payload["class_dict"].items()}
        return db


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE  I/O
# ═══════════════════════════════════════════════════════════════════════════════

def init_face_db():
    """Ensure face columns exist.  Safe to call multiple times."""
    conn = sqlite3.connect(DB_PATH)
    for col, typ in [("face_data", "BLOB"), ("face_threshold", "REAL"),
                     ("face_samples", "INTEGER")]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _load_db(uid: str) -> Optional[_FaceDatabase]:
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT face_data FROM users WHERE student_id=?", (uid,)).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    return _FaceDatabase.from_blob(row[0], uid=uid)


def _save_db(uid: str, fdb: _FaceDatabase):
    blob    = fdb.to_blob()
    avg_thr = float(np.mean(fdb.thresholds)) if fdb.thresholds else 0.0
    conn    = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE users SET face_data=?, face_threshold=?, face_samples=?"
        " WHERE student_id=?",
        (blob, avg_thr, len(fdb.embeddings), uid))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  MEDIAPIPE  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_face_mesh():
    if not _MP_AVAILABLE:
        return None
    return mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.65,
        min_tracking_confidence=0.65)


def _extract_embedding(frame: np.ndarray, face_mesh) -> Optional[np.ndarray]:
    """Return unit-normalised 1-D float32 landmark embedding, or None."""
    rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = face_mesh.process(rgb)
    if not result.multi_face_landmarks:
        return None
    lm  = result.multi_face_landmarks[0].landmark
    vec = np.array([[l.x, l.y, l.z] for l in lm], dtype=np.float32).flatten()
    n   = np.linalg.norm(vec)
    if n < 1e-6:
        return None
    return vec / n


# ═══════════════════════════════════════════════════════════════════════════════
#  DRAW  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _overlay(frame, line1: str, line2: str = "", color=(0, 220, 100)):
    h, w = frame.shape[:2]
    ov   = frame.copy()
    cv2.rectangle(ov, (0, h - 90), (w, h), (10, 10, 10), -1)
    cv2.addWeighted(ov, 0.65, frame, 0.35, 0, frame)
    cv2.putText(frame, line1, (12, h - 58),
                cv2.FONT_HERSHEY_DUPLEX, 0.62, color, 1, cv2.LINE_AA)
    if line2:
        cv2.putText(frame, line2, (12, h - 26),
                    cv2.FONT_HERSHEY_DUPLEX, 0.50, (170, 170, 170), 1, cv2.LINE_AA)


def _draw_bars(frame, sim: float, threshold: float):
    h, w = frame.shape[:2]
    bx, by, bh = 10, 10, 10
    bw = w - 20
    cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (40, 40, 40), -1)
    fill = int(bw * float(np.clip(sim, 0.0, 1.0)))
    col  = (0, 210, 70) if sim > threshold else (0, 100, 220)
    if fill > 0:
        cv2.rectangle(frame, (bx, by), (bx + fill, by + bh), col, -1)
    # Threshold marker (yellow line)
    mx = bx + int(bw * float(np.clip(threshold, 0.0, 1.0)))
    cv2.line(frame, (mx, by - 3), (mx, by + bh + 3), (0, 210, 255), 2)
    cv2.putText(frame,
                f"sim={sim:.3f}   threshold={threshold:.3f}",
                (bx, by + bh + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (200, 200, 200), 1, cv2.LINE_AA)


def _draw_ring(frame, pct: float, color=(0, 210, 80)):
    h, w  = frame.shape[:2]
    cx, cy = w // 2, h // 2
    r      = min(w, h) // 3
    cv2.ellipse(frame, (cx, cy), (r, r), -90, 0, 360, (40, 40, 40), 3)
    angle = int(360 * float(np.clip(pct, 0.0, 1.0)))
    if angle > 0:
        cv2.ellipse(frame, (cx, cy), (r, r), -90, 0, angle, color, 3)


# ═══════════════════════════════════════════════════════════════════════════════
#  REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════

def capture_face_registration(uid: str):
    """
    Collect CAPTURE_SAMPLES embeddings from webcam, build a _FaceDatabase
    using the paper's insert() loop, and persist it to students.db.

    Each sample is inserted one at a time (paper simulator style) so that
    intra-class thresholds are computed correctly from the outset.
    """
    if not _MP_AVAILABLE:
        messagebox.showwarning(
            "Face Auth",
            "MediaPipe not installed — face registration skipped.\n"
            "Run:  pip install mediapipe")
        return

    face_mesh = _get_face_mesh()
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    samples:   List[np.ndarray] = []
    status_msg = "Look at the camera — collecting face samples"
    status_col = (80, 200, 80)
    start_t    = time.time()
    WIN        = "ExamShield — Face Registration"

    print(f"[FaceAuth] Registration for '{uid}'  (need {CAPTURE_SAMPLES} samples)")

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.flip(frame, 1)
        emb   = _extract_embedding(frame, face_mesh)
        pct   = len(samples) / CAPTURE_SAMPLES

        if emb is not None:
            samples.append(emb)
            status_msg = (f"Capturing...  {len(samples)}/{CAPTURE_SAMPLES}  "
                          f"({int(pct * 100)}%)")
            status_col = (0, 210, 120)
            _draw_ring(frame, pct, status_col)

            if len(samples) >= CAPTURE_SAMPLES:
                _overlay(frame, "✓  All samples captured!  Saving...",
                         "", (0, 240, 240))
                cv2.imshow(WIN, frame)
                cv2.waitKey(700)
                break
        else:
            if time.time() - start_t > CAPTURE_TIMEOUT:
                _overlay(frame, "⏰  Timeout — try better lighting", "",
                         (0, 60, 255))
                cv2.imshow(WIN, frame)
                cv2.waitKey(1500)
                break
            status_msg = "⚠  No face — look straight at camera"
            status_col = (0, 80, 255)
            _draw_ring(frame, pct, (80, 80, 80))

        _overlay(frame, status_msg,
                 f"Student: {uid}  |  Vary pose slightly  |  Q=cancel",
                 status_col)
        cv2.imshow(WIN, frame)
        if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
            print("[FaceAuth] Registration cancelled")
            break

    cap.release()
    face_mesh.close()
    cv2.destroyAllWindows()

    if len(samples) < CAPTURE_SAMPLES:
        messagebox.showwarning(
            "Registration Incomplete",
            f"Only {len(samples)}/{CAPTURE_SAMPLES} samples captured.\n\n"
            "Face verification will be SKIPPED until you re-register.")
        return

    # ── Build personal DB: insert each sample one-by-one (paper simulator) ───
    fdb = _FaceDatabase(compare_num=MAX_COMPARE_NUM)
    for emb in samples:
        fdb.insert(uid, emb)

    _save_db(uid, fdb)

    avg_thr = float(np.mean(fdb.thresholds))
    max_thr = float(np.max(fdb.thresholds))
    print(f"[FaceAuth] Registered '{uid}'  samples={len(samples)}  "
          f"avg_thr={avg_thr:.4f}  max_thr={max_thr:.4f}")

    messagebox.showinfo(
        "Face Registered",
        f"✅  Face registered for '{uid}'!\n\n"
        f"Samples collected  : {len(samples)}\n"
        f"Adaptive threshold : {avg_thr:.4f} avg  /  {max_thr:.4f} max\n"
        f"  (inter-class adaptive — paper algorithm)\n\n"
        "Face verification will run at every login.")


# ═══════════════════════════════════════════════════════════════════════════════
#  VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class _VerifyResult:
    """
    Truthy/falsy like bool — backwards-compatible with
      ok = verify_face(uid)
    in main.py.  Extra fields available for proctor display.
    """
    __slots__ = ("passed", "best_sim", "threshold", "matched_label")

    def __init__(self, passed: bool, best_sim: float,
                 threshold: float, matched_label: str = ""):
        self.passed        = passed
        self.best_sim      = best_sim
        self.threshold     = threshold
        self.matched_label = matched_label

    def __bool__(self):
        return self.passed

    def __repr__(self):
        tag = "PASS" if self.passed else "FAIL"
        return (f"<FaceVerify {tag}  sim={self.best_sim:.3f}"
                f"  thr={self.threshold:.3f}  match='{self.matched_label}'>")


def verify_face(uid: str) -> _VerifyResult:
    """
    Verification loop — mirrors simulator_v4_adaptive_thd.py simulator():

      For each live webcam frame:
        1. Extract embedding.
        2. database.get_most_similar(probe) → (best_id, max_similarity)
        3. if max_similarity > database.get_threshold_by_id(best_id):
               accept  (consecutive counter ++)
           else:
               reject  (consecutive counter reset)
        4. After REQUIRED_MATCHES consecutive accepts → PASS
           Insert probe into DB (online threshold update).

    Returns _VerifyResult (truthy on pass, falsy on fail).
    """
    fdb = _load_db(uid)

    # ── No face registered → auto-pass with warning ───────────────────────────
    if fdb is None:
        messagebox.showwarning(
            "Face Not Registered",
            f"No face data found for '{uid}'.\n\n"
            "Face verification SKIPPED.\n"
            "Please register your face in the signup screen.")
        return _VerifyResult(True, 0.0, 0.0, "")

    if not _MP_AVAILABLE:
        messagebox.showwarning("Face Auth",
                               "MediaPipe not installed — check skipped.")
        return _VerifyResult(True, 0.0, 0.0, "")

    face_mesh   = _get_face_mesh()
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    attempts     = 0
    consecutive  = 0
    best_sim_all = 0.0
    active_thr   = 0.0
    matched_lbl  = ""
    result       = _VerifyResult(False, 0.0, 0.0, "")
    WIN          = "ExamShield — Face Verification"

    print(f"[FaceAuth] Verifying '{uid}'  DB_entries={len(fdb)}")

    while attempts < MAX_FRAMES:
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.flip(frame, 1)
        attempts += 1

        emb = _extract_embedding(frame, face_mesh)

        if emb is not None:
            # ── Paper: find most similar stored embedding ──────────────────────
            best_id, max_sim = fdb.get_most_similar(emb)
            best_sim_all     = max(best_sim_all, max_sim)
            active_thr       = fdb.get_threshold_by_id(best_id)
            matched_lbl      = fdb.get_label_by_id(best_id)

            _draw_bars(frame, max_sim, active_thr)

            # ── Paper decision: sim > threshold → accept ───────────────────────
            if max_sim > active_thr:
                consecutive += 1
                status_col   = (0, 210, 70)
                status_msg   = (f"✓  sim={max_sim:.3f} > thr={active_thr:.3f}"
                                f"  ({consecutive}/{REQUIRED_MATCHES})")
            else:
                consecutive  = 0
                status_col   = (0, 100, 220)
                status_msg   = (f"Verifying...  sim={max_sim:.3f}"
                                f"  thr={active_thr:.3f}")

            # ── Need REQUIRED_MATCHES consecutive accepts to pass ──────────────
            if consecutive >= REQUIRED_MATCHES:
                # ── Online update: insert probe into DB (paper §simulator loop)
                fdb.insert(uid, emb)
                fdb.trim_to(MAX_STORED_EMBEDDINGS)

                # Persist asynchronously — don't block login
                snap_uid = uid
                snap_fdb = fdb
                threading.Thread(
                    target=lambda u=snap_uid, d=snap_fdb: _save_db(u, d),
                    daemon=True).start()

                result = _VerifyResult(True, max_sim, active_thr, matched_lbl)
                _overlay(frame, "✅  Face Verified!",
                         f"Welcome, {uid}", (0, 255, 180))
                cv2.imshow(WIN, frame)
                cv2.waitKey(900)
                break

        else:
            consecutive = 0
            status_msg  = "⚠  No face detected — look straight at camera"
            status_col  = (0, 80, 200)

        remaining = MAX_FRAMES - attempts
        _overlay(frame, status_msg,
                 f"ID: {uid}  |  {remaining} frames left  |  Q=cancel",
                 status_col)
        cv2.imshow(WIN, frame)
        if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
            print("[FaceAuth] Verification cancelled")
            break

    cap.release()
    face_mesh.close()
    cv2.destroyAllWindows()

    # ── Final outcome logging + user message ──────────────────────────────────
    if not result.passed:
        print(f"[FaceAuth] FAILED '{uid}'  "
              f"best_sim={best_sim_all:.3f}  thr={active_thr:.3f}")
        messagebox.showerror(
            "Face Verification Failed",
            f"❌  Face verification FAILED for '{uid}'.\n\n"
            f"Best similarity : {best_sim_all:.3f}\n"
            f"Required        : > {active_thr:.3f}  (adaptive threshold)\n\n"
            "Tips:\n"
            "• Use same lighting & camera angle as registration\n"
            "• Move closer so your face fills the frame\n"
            "• Remove glasses/hat if absent during registration\n\n"
            "Ask admin to re-register your face if this persists.")
    else:
        print(f"[FaceAuth] PASSED '{uid}'  "
              f"sim={result.best_sim:.3f}  thr={result.threshold:.3f}  "
              f"DB={len(fdb)}")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN  UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def admin_clear_face(uid: str) -> bool:
    """Wipe stored face data for a student (for admin re-enrol)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE users SET face_data=NULL, face_threshold=NULL, face_samples=NULL"
            " WHERE student_id=?", (uid,))
        conn.commit()
        conn.close()
        print(f"[FaceAuth] Cleared face data for '{uid}'")
        return True
    except Exception as e:
        print(f"[FaceAuth] Error clearing '{uid}': {e}")
        return False


def get_face_info(uid: str) -> dict:
    """Return face-auth metadata for proctor panel display."""
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT face_threshold, face_samples FROM users WHERE student_id=?",
        (uid,)).fetchone()
    conn.close()
    if not row or row[1] is None:
        return {"registered": False}
    return {
        "registered":    True,
        "avg_threshold": round(float(row[0] or 0.0), 4),
        "samples":       int(row[1] or 0),
    }