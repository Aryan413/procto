from __future__ import division
import cv2
from .pupil import Pupil


class Calibration:
    """
    Calibrates the pupil detection algorithm by finding the optimal
    binarization threshold for each eye independently.

    Collects nb_frames worth of samples before locking in the threshold.
    """

    def __init__(self):
        self.nb_frames        = 20
        self.thresholds_left  = []
        self.thresholds_right = []

    def is_complete(self):
        """True once enough calibration frames have been gathered."""
        return (
            len(self.thresholds_left)  >= self.nb_frames and
            len(self.thresholds_right) >= self.nb_frames
        )

    def threshold(self, side):
        """
        Returns the averaged calibrated threshold for the given eye.
        side: 0 = left, 1 = right
        """
        if side == 0 and self.thresholds_left:
            return int(sum(self.thresholds_left) / len(self.thresholds_left))
        elif side == 1 and self.thresholds_right:
            return int(sum(self.thresholds_right) / len(self.thresholds_right))
        return 50  # sensible default while warming up

    @staticmethod
    def iris_size(frame):
        """
        Returns the fraction of the eye frame occupied by the dark iris blob.
        Used to evaluate how well a given threshold isolates the iris.
        """
        frame = frame[5:-5, 5:-5]
        height, width = frame.shape[:2]
        nb_pixels = height * width
        nb_blacks  = nb_pixels - cv2.countNonZero(frame)
        return nb_blacks / nb_pixels

    @staticmethod
    def find_best_threshold(eye_frame):
        """
        Sweeps threshold values 5–100 and picks the one whose
        resulting iris size is closest to the expected average (0.48).
        """
        average_iris_size = 0.48
        trials = {}
        for threshold in range(5, 100, 5):
            iris_frame = Pupil.image_processing(eye_frame, threshold)
            trials[threshold] = Calibration.iris_size(iris_frame)
        best_threshold, _ = min(
            trials.items(),
            key=lambda p: abs(p[1] - average_iris_size)
        )
        return best_threshold

    def evaluate(self, eye_frame, side):
        """
        Adds one calibration sample for the given eye side.
        Called automatically while is_complete() is False.
        """
        if eye_frame is None or eye_frame.size == 0:
            return
        threshold = self.find_best_threshold(eye_frame)
        if side == 0:
            self.thresholds_left.append(threshold)
        elif side == 1:
            self.thresholds_right.append(threshold)