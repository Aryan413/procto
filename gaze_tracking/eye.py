import math
import numpy as np
import cv2
from .pupil import Pupil


class Eye:
    """
    Isolates one eye from the full face frame and runs pupil detection on it.

    Exposes:
        self.frame   – cropped greyscale eye image
        self.origin  – (x, y) top-left corner of the crop in the original frame
        self.center  – (cx, cy) centre of the crop
        self.pupil   – Pupil instance with .x / .y
        self.blinking – blinking ratio (width/height); high value = closed eye
    """

    LEFT_EYE_POINTS  = [36, 37, 38, 39, 40, 41]
    RIGHT_EYE_POINTS = [42, 43, 44, 45, 46, 47]

    def __init__(self, original_frame, landmarks, side, calibration):
        self.frame    = None
        self.origin   = None
        self.center   = None
        self.pupil    = None
        self.blinking = None
        self._analyze(original_frame, landmarks, side, calibration)

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _middle_point(p1, p2):
        return (int((p1.x + p2.x) / 2), int((p1.y + p2.y) / 2))

    def _isolate(self, frame, landmarks, points):
        """Carve out the eye region, masked and cropped."""
        region = np.array(
            [(landmarks.part(p).x, landmarks.part(p).y) for p in points],
            dtype=np.int32
        )
        height, width = frame.shape[:2]

        # White mask → keep only the eye polygon
        mask      = np.full((height, width), 255, np.uint8)
        black_frame = np.zeros((height, width), np.uint8)
        cv2.fillPoly(mask, [region], 0)
        eye = cv2.bitwise_not(black_frame, frame.copy(), mask=mask)

        margin = 5
        min_x = max(np.min(region[:, 0]) - margin, 0)
        max_x = min(np.max(region[:, 0]) + margin, width)
        min_y = max(np.min(region[:, 1]) - margin, 0)
        max_y = min(np.max(region[:, 1]) + margin, height)

        self.frame  = eye[min_y:max_y, min_x:max_x]
        self.origin = (min_x, min_y)
        h, w        = self.frame.shape[:2]
        self.center = (w / 2, h / 2)

    def _blinking_ratio(self, landmarks, points):
        """
        Eye aspect ratio — high value (>3.8) indicates a closed eye.
        Computed as horizontal_span / vertical_span.
        """
        left   = (landmarks.part(points[0]).x, landmarks.part(points[0]).y)
        right  = (landmarks.part(points[3]).x, landmarks.part(points[3]).y)
        top    = self._middle_point(landmarks.part(points[1]), landmarks.part(points[2]))
        bottom = self._middle_point(landmarks.part(points[5]), landmarks.part(points[4]))

        eye_width  = math.hypot(left[0] - right[0], left[1] - right[1])
        eye_height = math.hypot(top[0] - bottom[0], top[1] - bottom[1])
        try:
            return eye_width / eye_height
        except ZeroDivisionError:
            return None

    # ── main ───────────────────────────────────────────────────────────────

    def _analyze(self, original_frame, landmarks, side, calibration):
        if side == 0:
            points = self.LEFT_EYE_POINTS
        elif side == 1:
            points = self.RIGHT_EYE_POINTS
        else:
            return

        self.blinking = self._blinking_ratio(landmarks, points)
        self._isolate(original_frame, landmarks, points)

        if not calibration.is_complete():
            calibration.evaluate(self.frame, side)

        threshold  = calibration.threshold(side)
        self.pupil = Pupil(self.frame, threshold)