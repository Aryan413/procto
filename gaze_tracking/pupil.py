import cv2
import numpy as np


class Pupil:
    """
    Detects the position of a pupil inside an isolated eye frame
    using blob detection on a binarized image.
    """

    def __init__(self, eye_frame, threshold):
        self.iris_frame = None
        self.threshold  = threshold
        self.x = None
        self.y = None
        self._detect(eye_frame, threshold)

    @staticmethod
    def image_processing(eye_frame, threshold):
        """
        Performs binarization + morphological cleanup on the eye frame
        so the iris stands out as a solid black blob.
        """
        kernel = np.ones((3, 3), np.uint8)
        new_frame = cv2.bilateralFilter(eye_frame, 10, 15, 15)
        new_frame = cv2.erode(new_frame, kernel, iterations=3)
        new_frame = cv2.threshold(new_frame, threshold, 255, cv2.THRESH_BINARY)[1]
        return new_frame

    def _detect(self, eye_frame, threshold):
        """
        Locates the pupil centroid using contour analysis on the
        processed (binarized) eye frame.
        """
        if eye_frame is None or eye_frame.size == 0:
            return

        self.iris_frame = self.image_processing(eye_frame, threshold)

        # Find contours on the inverted frame (pupil = dark blob)
        contours, _ = cv2.findContours(
            cv2.bitwise_not(self.iris_frame),
            cv2.RETR_TREE,
            cv2.CHAIN_APPROX_NONE
        )

        if not contours:
            return

        # Pick the largest contour — most likely the iris/pupil
        largest = max(contours, key=cv2.contourArea)
        moments = cv2.moments(largest)

        if moments["m00"] != 0:
            self.x = int(moments["m10"] / moments["m00"])
            self.y = int(moments["m01"] / moments["m00"])