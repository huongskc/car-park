"""Run: python -m unittest discover -s tests -p test_alpr_integration.py -v

No database, broker, webcam or model required.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.enums import UserRole
from app.routers.alpr import router
from app.schemas.alpr_schema import CaptureRequest
from app.services.alpr_service import alpr_service


class ALPRIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.key = patch.object(settings, "INTERNAL_ALPR_API_KEY", "test-private-key")
        self.key.start()
        self.addCleanup(self.key.stop)
        self.requests = []
        self.mode = "ok"
        real_client = httpx.AsyncClient

        def upstream(request):
            import json
            self.requests.append(request)
            self.assertEqual(request.headers["X-API-Key"], "test-private-key")
            if self.mode == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            if self.mode == "offline":
                raise httpx.ConnectError("offline", request=request)
            if isinstance(self.mode, int):
                return httpx.Response(self.mode)
            if request.url.path.endswith("/capture"):
                data = json.loads(request.content)
                if self.mode == "bad_json":
                    return httpx.Response(200, text="invalid")
                if self.mode == "mismatch":
                    data["request_id"] = "other"
                return httpx.Response(200, json={
                    **data, "status": "success" if self.mode != "no_text" else "no_text_detected",
                    "image_id": "capture_0123abcd.jpg", "plate_number": "30A12345" if self.mode != "no_text" else None,
                    "confidence": .95, "needs_confirmation": True,
                    "rejection_reasons": [],
                })
            return httpx.Response(200, content=b"jpeg-content", headers={"Content-Type": "image/jpeg"})

        self.client_patch = patch("app.services.alpr_service.httpx.AsyncClient",
                                  side_effect=lambda **kw: real_client(transport=httpx.MockTransport(upstream), **kw))
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.app.dependency_overrides[get_db] = lambda: None
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=UserRole.OPERATOR)
        self.client = TestClient(self.app)

    def test_capture_contract_and_private_key(self):
        response = self.client.post("/api/v1/alpr/capture", json={"lane": "IN", "session_id": "42"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["plate_number"], "30A12345")
        self.assertTrue(data["needs_confirmation"])
        self.assertEqual(data["image_url"], "/api/v1/alpr/images/capture_0123abcd.jpg")
        self.assertNotIn("test-private-key", response.text)
        image = self.client.get(data["image_url"])
        self.assertEqual(image.content, b"jpeg-content")
        self.assertEqual(image.headers["cache-control"], "no-store")

    def test_no_text_is_not_a_fake_plate(self):
        self.mode = "no_text"
        result = self.client.post("/api/v1/alpr/capture", json={"lane": "OUT"})
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()["plate_number"])

    def test_errors_and_invalid_upstream(self):
        for mode, expected in [(409, 409), (401, 502), (500, 502), (503, 503),
                               ("timeout", 504), ("offline", 503),
                               ("bad_json", 502), ("mismatch", 502)]:
            with self.subTest(mode=mode):
                self.mode = mode
                result = self.client.post("/api/v1/alpr/capture", json={"lane": "IN"})
                self.assertEqual(result.status_code, expected)

    def test_access_and_validation(self):
        self.app.dependency_overrides.clear()
        self.app.dependency_overrides[get_db] = lambda: None
        self.assertEqual(self.client.post("/api/v1/alpr/capture", json={"lane": "IN"}).status_code, 401)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=UserRole.VIEWER)
        self.assertEqual(self.client.post("/api/v1/alpr/capture", json={"lane": "IN"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/alpr/images/capture_0123abcd.jpg").status_code, 403)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=UserRole.ADMIN)
        self.assertEqual(self.client.post("/api/v1/alpr/capture", json={"lane": "SIDE"}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/alpr/images/not-valid.jpg").status_code, 422)
        self.assertEqual(len(self.requests), 0)

    def test_missing_key_and_missing_image(self):
        with patch.object(settings, "INTERNAL_ALPR_API_KEY", ""):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(alpr_service.capture(CaptureRequest(lane="IN")))
            self.assertEqual(error.exception.status_code, 503)
        self.mode = 404
        self.assertEqual(self.client.get("/api/v1/alpr/images/capture_0123abcd.jpg").status_code, 404)


if __name__ == "__main__":
    unittest.main()
