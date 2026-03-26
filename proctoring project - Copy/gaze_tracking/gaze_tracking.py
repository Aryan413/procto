from __future__ import division
import cv2
import numpy as np
import mediapipe as mp
from collections import deque


class GazeTracking:
    """
    Look-away detection using HEAD POSE (yaw/pitch) + iris ratio combined.
    Head pose is far more reliable than iris alone for exam proctoring.
    
    Direction is triggered when the student physically turns their head
    OR moves their eyes significantly away from centre.
    """

    # ── Smoothing ──────────────────────────────────────────────────────────
    SMOOTH_WINDOW = 6   # frames to average before deciding direction

    # ── Head pose thresholds ──────────────────────────────────────────────
    # Only yaw (left/right) is used - pitch/up/down disabled
    # 80 degrees approx maps to ~0.18 normalised yaw offset
    HEAD_YAW_THRESH   = 0.42   # maximum practical threshold - fully sideways profile view only
    HEAD_PITCH_THRESH = 9999   # disabled

    # ── Iris thresholds ───────────────────────────────────────────────────
    # Only triggers when eye is pushed to absolute corner - near 180 deg equiv
    H_LEFT_THRESH  = 0.88   # absolute far left corner only
    H_RIGHT_THRESH = 0.12   # absolute far right corner only
    V_UP_THRESH    = 9999   # disabled
    V_DOWN_THRESH  = 9999   # disabled

    # MediaPipe indices
    _LEFT_IRIS          = [474, 475, 476, 477]
    _RIGHT_IRIS         = [469, 470, 471, 472]
    _LEFT_EYE_CORNERS   = [33,  133]
    _RIGHT_EYE_CORNERS  = [362, 263]
    _LEFT_EYE_TB        = [159, 145]
    _RIGHT_EYE_TB       = [386, 374]
    _NOSE_TIP           = 1
    _CHIN               = 152
    _LEFT_TEMPLE        = 234
    _RIGHT_TEMPLE       = 454
    _FOREHEAD           = 10

    def __init__(self):
        self.frame         = None
        self._h_ratio      = None
        self._v_ratio      = None
        self._yaw          = 0.0
        self._pitch        = 0.0
        self._pupils_found = False
        self._left_iris    = None
        self._right_iris   = None

        # Smoothing buffers
        self._h_buf     = deque(maxlen=self.SMOOTH_WINDOW)
        self._v_buf     = deque(maxlen=self.SMOOTH_WINDOW)
        self._yaw_buf   = deque(maxlen=self.SMOOTH_WINDOW)
        self._pitch_buf = deque(maxlen=self.SMOOTH_WINDOW)

        class _Cal:
            def is_complete(self): return True
        self.calibration = _Cal()

        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    # ── public API ─────────────────────────────────────────────────────────

    @property
    def pupils_located(self):
        return self._pupils_found

    def refresh(self, frame):
        self.frame = frame
        self._analyze()

    def horizontal_ratio(self):
        return self._h_ratio

    def vertical_ratio(self):
        return self._v_ratio

    def is_left(self):
        # HEAD YAW ONLY — iris ignored completely
        # Triggers only when student fully turns head sideways (~180 deg)
        if len(self._yaw_buf) < self.SMOOTH_WINDOW:
            return False
        return np.mean(self._yaw_buf) < -self.HEAD_YAW_THRESH

    def is_right(self):
        # HEAD YAW ONLY — iris ignored completely
        if len(self._yaw_buf) < self.SMOOTH_WINDOW:
            return False
        return np.mean(self._yaw_buf) > self.HEAD_YAW_THRESH

    def is_up(self):
        return False   # disabled - only left/right used

    def is_down(self):
        return False   # disabled - only left/right used

    def is_center(self):
        return (self._pupils_found and
                not self.is_left()  and not self.is_right() and
                not self.is_up()    and not self.is_down())

    def is_blinking(self):
        return False

    def direction(self):
        if not self._pupils_found:
            return "undetected"
        if self.is_left():  return "left"
        if self.is_right(): return "right"
        if self.is_up():    return "up"
        if self.is_down():  return "down"
        return "center"

    def pupil_left_coords(self):
        if self._pupils_found and self._left_iris is not None:
            return (int(self._left_iris[0]), int(self._left_iris[1]))
        return None

    def pupil_right_coords(self):
        if self._pupils_found and self._right_iris is not None:
            return (int(self._right_iris[0]), int(self._right_iris[1]))
        return None

    def annotated_frame(self):
        return self.frame.copy()

    # ── internal ───────────────────────────────────────────────────────────

    def _analyze(self):
        self._pupils_found = False
        self._h_ratio      = None
        self._v_ratio      = None

        rgb     = cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB)
        results = self._face_mesh.process(rgb)

        if not results.multi_face_landmarks:
            return

        lm   = results.multi_face_landmarks[0].landmark
        h, w = self.frame.shape[:2]

        def pt(idx):
            return np.array([lm[idx].x * w, lm[idx].y * h])

        def pn(idx):
            return np.array([lm[idx].x, lm[idx].y])

        # ── Iris ratios ───────────────────────────────────────────────────
        left_iris  = np.mean([pt(i) for i in self._LEFT_IRIS],  axis=0)
        right_iris = np.mean([pt(i) for i in self._RIGHT_IRIS], axis=0)

        ll, lr = pt(self._LEFT_EYE_CORNERS[0]),  pt(self._LEFT_EYE_CORNERS[1])
        rl, rr = pt(self._RIGHT_EYE_CORNERS[0]), pt(self._RIGHT_EYE_CORNERS[1])

        lh = (left_iris[0]  - ll[0]) / (lr[0] - ll[0] + 1e-6)
        rh = (right_iris[0] - rl[0]) / (rr[0] - rl[0] + 1e-6)
        raw_h = float(np.clip((lh + rh) / 2, 0.0, 1.0))

        tl, bl = pt(self._LEFT_EYE_TB[0]),  pt(self._LEFT_EYE_TB[1])
        tr, br = pt(self._RIGHT_EYE_TB[0]), pt(self._RIGHT_EYE_TB[1])
        lv = (left_iris[1]  - tl[1]) / (bl[1] - tl[1] + 1e-6)
        rv = (right_iris[1] - tr[1]) / (br[1] - tr[1] + 1e-6)
        raw_v = float(np.clip((lv + rv) / 2, 0.0, 1.0))

        self._h_buf.append(raw_h)
        self._v_buf.append(raw_v)
        self._h_ratio = float(np.mean(self._h_buf))
        self._v_ratio = float(np.mean(self._v_buf))

        # ── Head pose ─────────────────────────────────────────────────────
        nose     = pn(self._NOSE_TIP)
        lt       = pn(self._LEFT_TEMPLE)
        rt       = pn(self._RIGHT_TEMPLE)
        forehead = pn(self._FOREHEAD)
        chin     = pn(self._CHIN)

        face_cx = (lt[0] + rt[0]) / 2
        face_cy = (forehead[1] + chin[1]) / 2

        # Normalise by face width so distance from camera doesn't matter
        face_w  = abs(rt[0] - lt[0]) + 1e-6
        face_ht = abs(chin[1] - forehead[1]) + 1e-6

        yaw   = (nose[0] - face_cx) / face_w    # negative=left, positive=right
        pitch = (nose[1] - face_cy) / face_ht   # negative=up,   positive=down

        self._yaw_buf.append(yaw)
        self._pitch_buf.append(pitch)
        self._yaw   = float(np.mean(self._yaw_buf))
        self._pitch = float(np.mean(self._pitch_buf))

        self._pupils_found = True
        self._left_iris    = left_iris
        self._right_iris   = right_iris