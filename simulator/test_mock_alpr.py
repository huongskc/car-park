import io
import os
import unittest

from fastapi import UploadFile

os.environ.setdefault("ALPR_API_KEY", "test-mock-key")
from simulator import mock_alpr  # noqa: E402


class MockALPRContractTest(unittest.TestCase):
    def test_snapshot_then_recognize(self):
        key = os.environ["ALPR_API_KEY"]
        snapshot = mock_alpr.snapshot(mock_alpr.Capture(
            request_id="snapshot1", session_id="SES_1", lane="IN"), key)
        self.assertTrue(snapshot.body.startswith(b"\xff\xd8\xff"))
        self.assertTrue(snapshot.headers["x-image-id"].endswith(".jpg"))
        result = mock_alpr.recognize("recognize1", "IN",
                                     UploadFile(file=io.BytesIO(snapshot.body), filename="frame.jpg"),
                                     "SES_1", key)
        self.assertEqual(result["plate_number"], "30A12345")


if __name__ == "__main__":
    unittest.main()
