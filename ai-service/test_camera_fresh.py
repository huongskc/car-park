import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import main
from main import LiveCamera


class FreshFrameTest(unittest.TestCase):
    def test_capture_never_returns_old_frame_when_camera_stalls(self):
        camera = LiveCamera()
        camera._frame = np.zeros((2, 2, 3), dtype=np.uint8)
        camera._frame_seq = 1
        self.assertIsNone(camera.fresh_frame(skip_count=1, timeout=0.01))

        def publish_new_frame():
            time.sleep(0.02)
            with camera._cond:
                camera._frame = np.ones((2, 2, 3), dtype=np.uint8)
                camera._frame_seq += 1
                camera._cond.notify_all()

        worker = threading.Thread(target=publish_new_frame)
        worker.start()
        fresh = camera.fresh_frame(skip_count=1, timeout=0.2)
        worker.join()
        self.assertTrue(np.all(fresh == 1))

    def test_snapshot_returns_saved_jpeg_without_waiting_for_ocr(self):
        frame = np.full((20, 40, 3), 200, dtype=np.uint8)
        request = main.CaptureRequest(request_id='snapshot_test', lane='IN', session_id='SES_1')
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(main, 'IMAGE_DIR', Path(directory)), patch.object(main, 'snap', return_value=frame):
                response = main.snapshot(request)
                image = Path(directory) / response.headers['x-image-id']
                self.assertTrue(image.is_file())
                self.assertEqual(response.body, image.read_bytes())
                self.assertTrue(response.body.startswith(b'\xff\xd8\xff'))

    def test_ocr_uses_snapshot_lane_without_cropping_again(self):
        frame = np.full((20, 40, 3), 200, dtype=np.uint8)
        request = main.CaptureRequest(request_id='snapshot_test', lane='IN', session_id='SES_1')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'IMAGE_DIR', Path(directory)), \
                patch.object(main, 'roi_for', side_effect=AssertionError('double ROI crop')), \
                patch.object(main, 'detect_displayed_plate', return_value=None), \
                patch.object(main, 'detect_plate_bbox', return_value=None):
            result = main.process_frame(request, frame, already_cropped=True)
            self.assertEqual(result['status'], 'no_text_detected')
            self.assertTrue((Path(directory) / result['image_id']).is_file())


if __name__ == '__main__':
    unittest.main()
