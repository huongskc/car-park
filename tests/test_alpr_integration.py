"""Run: python -m unittest discover -s tests -p test_alpr_integration.py -v

No database, broker, webcam or model required.
"""
import asyncio
from email import policy
from email.parser import BytesParser
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
            elif request.url.path.endswith("/recognize"):
                message = BytesParser(policy=policy.default).parsebytes(
                    b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
                )
                self.uploaded_parts = {
                    part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                    for part in message.iter_parts()
                }
                data = {key: value.decode() for key, value in self.uploaded_parts.items() if key != "image"}
                data.setdefault("session_id", None)
            else:
                return httpx.Response(200, content=b"jpeg-content", headers={"Content-Type": "image/jpeg"})
            if request.url.path.endswith(("/capture", "/recognize")):
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

    def test_browser_frame_upload_and_lane_identity(self):
        response = self.client.post(
            "/api/v1/alpr/recognize", data={"lane": "OUT", "session_id": "42"},
            files={"image": ("camera.jpg", b"frame-jpeg-data", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lane"], "OUT")
        self.assertEqual(response.json()["session_id"], "42")
        self.assertEqual(self.uploaded_parts["image"], b"frame-jpeg-data")
        self.assertEqual(self.requests[0].headers["X-API-Key"], "test-private-key")
        self.assertNotIn("authorization", self.requests[0].headers)
        self.assertNotIn("test-private-key", response.text)

    def test_browser_frame_validation_without_upstream_requests(self):
        for content, media_type, expected in [
            (b"", "image/jpeg", 400), (b"pdf", "application/pdf", 415),
            (b"x" * (4 * 1024 * 1024 + 1), "image/jpeg", 413),
        ]:
            with self.subTest(expected=expected):
                response = self.client.post(
                    "/api/v1/alpr/recognize", data={"lane": "IN"},
                    files={"image": ("frame", content, media_type)},
                )
                self.assertEqual(response.status_code, expected)
        self.assertEqual(len(self.requests), 0)

    def test_browser_frame_authentication_and_invalid_result(self):
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=UserRole.VIEWER)
        response = self.client.post(
            "/api/v1/alpr/recognize", data={"lane": "IN"},
            files={"image": ("frame.jpg", b"jpeg", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.requests), 0)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=UserRole.OPERATOR)
        for mode, status in [("mismatch", 502), ("no_text", 200), (400, 400), (413, 413)]:
            self.mode = mode
            response = self.client.post(
                "/api/v1/alpr/recognize", data={"lane": "IN"},
                files={"image": ("frame.jpg", b"jpeg", "image/jpeg")},
            )
            self.assertEqual(response.status_code, status)
            if mode == "no_text":
                self.assertIsNone(response.json()["plate_number"])

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
