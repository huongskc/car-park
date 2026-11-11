import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import main
from main import LiveCamera
from ocr import recognize_plate


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

    def test_gate_ocr_skips_slow_fallback_and_second_detector_when_confident(self):
        frame = np.full((40, 80, 3), 200, dtype=np.uint8)
        request = main.CaptureRequest(request_id='fast_test', lane='IN', session_id='SES_1')
        recognized = {'plate_number': '29Y303658', 'confidence': 0.99,
                      'needs_confirmation': False}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'IMAGE_DIR', Path(directory)), \
                patch.object(main, 'detect_displayed_plate', return_value=(5, 5, 60, 35)), \
                patch.object(main, 'detect_plate_bbox', side_effect=AssertionError('unneeded detector')), \
                patch.object(main, 'recognize_plate', return_value=recognized) as ocr:
            result = main.process_frame(request, frame, already_cropped=True)
            self.assertEqual(result['plate_number'], '29Y303658')
            self.assertFalse(ocr.call_args.kwargs['allow_heavy_fallback'])

    def test_disabling_heavy_fallback_overrides_demo_config(self):
        uncertain = {'plate_number': None, 'suggested_plate': None,
                     'valid_format': False, 'confidence': 0.1}
        with patch('ocr._recognize_specialist', return_value=uncertain), \
                patch('ocr._recognize_easyocr', side_effect=AssertionError('slow fallback')):
            result = recognize_plate(np.zeros((20, 40, 3), dtype=np.uint8),
                                     engine='plate', allow_heavy_fallback=False)
            self.assertIsNone(result['plate_number'])


if __name__ == '__main__':
    unittest.main()
