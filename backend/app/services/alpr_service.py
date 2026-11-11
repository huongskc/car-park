"""Authenticated backend-to-edge ALPR client. Never substitutes fake plates."""
import re
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import settings
from app.schemas.alpr_schema import CaptureRequest, CaptureResult, IMAGE_ID_PATTERN


class ALPRService:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._client_loop = None

    def _get_client(self) -> httpx.AsyncClient:
        import asyncio
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if (
            self._client is None
            or self._client.is_closed
            or (loop is not None and self._client_loop != loop)
        ):
            self._client = httpx.AsyncClient(
                base_url=settings.ALPR_SERVICE_URL.rstrip("/") + "/",
                headers={"X-API-Key": settings.INTERNAL_ALPR_API_KEY},
                timeout=settings.ALPR_TIMEOUT_SEC,
                follow_redirects=False,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
            self._client_loop = loop
        return self._client

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not settings.INTERNAL_ALPR_API_KEY.strip():
            raise HTTPException(503, "Chưa cấu hình INTERNAL_ALPR_API_KEY cho backend.")
        client = self._get_client()
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise HTTPException(504, "AI service xử lý quá thời gian cho phép.") from None
        except httpx.RequestError:
            raise HTTPException(503, "Không kết nối được AI service.") from None
        if response.status_code == 409:
            raise HTTPException(409, "Camera đang bận. Vui lòng thử lại.")
        if response.status_code == 404 and path.startswith("images/"):
            raise HTTPException(404, "Ảnh không tồn tại.")
        if response.status_code == 503:
            raise HTTPException(503, "Camera hoặc AI service chưa sẵn sàng.")
        if path == "recognize" and response.status_code in (400, 413, 415):
            raise HTTPException(response.status_code, "Ảnh gửi lên không hợp lệ hoặc vượt giới hạn cho phép.")
        if response.status_code != 200:
            # Do not expose upstream errors, credentials or internal URLs.
            raise HTTPException(502, "AI service trả về lỗi hoặc xác thực không thành công.")
        return response

    async def capture(self, payload: CaptureRequest) -> CaptureResult:
        request_id = uuid4().hex
        response = await self._request(
            "POST", "capture", json={**payload.model_dump(), "request_id": request_id}
        )
        return self._result(response, payload, request_id)

    async def snapshot(self, payload: CaptureRequest) -> tuple[str, bytes]:
        response = await self._request(
            "POST", "snapshot", json={**payload.model_dump(), "request_id": uuid4().hex}
        )
        image_id = response.headers.get("x-image-id", "")
        if not re.fullmatch(IMAGE_ID_PATTERN, image_id) or not response.content.startswith(b"\xff\xd8\xff"):
            raise HTTPException(502, "AI service trả về ảnh chụp không hợp lệ.")
        return f"/api/v1/alpr/images/{image_id}", response.content

    async def recognize(self, payload: CaptureRequest, content: bytes, content_type: str,
                        lane_crop: bool = False) -> CaptureResult:
        request_id = uuid4().hex
        response = await self._request(
            "POST", "recognize",
            data={**payload.model_dump(exclude_none=True), "request_id": request_id,
                  "image_is_lane_crop": str(lane_crop).lower()},
            files={"image": ("frame.jpg" if content_type == "image/jpeg" else "frame.png", content, content_type)},
        )
        return self._result(response, payload, request_id)

    @staticmethod
    def _result(response: httpx.Response, payload: CaptureRequest, request_id: str) -> CaptureResult:
        try:
            result = CaptureResult.model_validate(response.json())
            if (result.request_id != request_id or result.lane != payload.lane
                    or result.session_id != payload.session_id):
                raise ValueError("Mismatched capture response")
        except (ValueError, ValidationError):
            raise HTTPException(502, "Kết quả AI service không hợp lệ.") from None
        result.image_url = f"/api/v1/alpr/images/{result.image_id}"
        return result

    async def preview(self, lane: str) -> bytes:
        response = await self._request("GET", "preview", params={"lane": lane}, timeout=2.0)
        if response.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise HTTPException(502, "AI service trả về ảnh không hợp lệ.")
        return response.content

    async def image(self, image_id: str) -> bytes:
        response = await self._request("GET", f"images/{image_id}")
        if response.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise HTTPException(502, "AI service trả về ảnh không hợp lệ.")
        return response.content


alpr_service = ALPRService()
