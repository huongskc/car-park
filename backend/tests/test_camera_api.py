"""Live camera transport retains the original parking-session contract."""
import asyncio

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.alpr_service import ALPRService, alpr_service


def login(client, username="operator", password="op123"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_video_requires_operator_and_original_capture_requires_a_session(monkeypatch):
    async def unexpected(_payload):
        pytest.fail("Unauthorized request reached AI")

    monkeypatch.setattr(alpr_service, "open_stream", unexpected)
    client = TestClient(app)
    assert client.get("/api/v1/alpr/stream?lane=IN").status_code == 401
    viewer = login(client, "viewer", "view123")
    assert client.get("/api/v1/alpr/stream?lane=IN", headers=viewer).status_code == 403
    operator = login(client)
    response = client.post("/api/v1/alpr/capture", headers=operator, json={"lane": "IN"})
    assert response.status_code == 400
    assert client.post("/api/v1/alpr/scan", headers=operator, json={"lane": "IN"}).status_code == 404


def test_video_forwards_frames_and_closes_upstream(monkeypatch):
    frame = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n\xff\xd8\xff\xd9\r\n"
    upstream = httpx.Response(200, headers={"content-type": "multipart/x-mixed-replace; boundary=frame"},
                              stream=httpx.ByteStream(frame + frame))

    async def open_stream(lane):
        assert lane == "OUT"
        return upstream

    monkeypatch.setattr(alpr_service, "open_stream", open_stream)
    client = TestClient(app)
    response = client.get("/api/v1/alpr/stream?lane=OUT", headers=login(client))
    assert response.status_code == 200
    assert response.content == frame + frame
    assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    assert response.headers["cache-control"] == "no-store"
    assert upstream.is_closed


@pytest.mark.parametrize("status,content_type,expected", [
    (503, "application/json", 503), (401, "application/json", 502), (200, "image/jpeg", 502),
])
def test_bad_upstream_is_rejected_and_closed(monkeypatch, status, content_type, expected):
    upstream = httpx.Response(status, headers={"content-type": content_type},
                              stream=httpx.ByteStream(b"not a stream"))

    def handler(request):
        assert request.url.path == "/stream" and request.url.params["lane"] == "IN"
        assert request.headers["X-API-Key"] == "test-camera-key"
        return upstream

    async def check():
        async with httpx.AsyncClient(base_url="http://camera/", headers={"X-API-Key": "test-camera-key"},
                                     transport=httpx.MockTransport(handler)) as client:
            service = ALPRService()
            monkeypatch.setattr(service, "_get_client", lambda: client)
            monkeypatch.setattr(settings, "INTERNAL_ALPR_API_KEY", "test-camera-key")
            with pytest.raises(HTTPException) as error:
                await service.open_stream("IN")
            assert error.value.status_code == expected
            assert upstream.is_closed

    asyncio.run(check())
