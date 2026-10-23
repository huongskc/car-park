"""Authenticated backend-to-edge ALPR client. Never substitutes fake plates."""
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import settings
from app.schemas.alpr_schema import CaptureRequest, CaptureResult


class ALPRService:
    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not settings.INTERNAL_ALPR_API_KEY.strip():
            raise HTTPException(503, "Chưa cấu hình INTERNAL_ALPR_API_KEY cho backend.")
        try:
            async with httpx.AsyncClient(
                base_url=settings.ALPR_SERVICE_URL.rstrip("/") + "/",
                headers={"X-API-Key": settings.INTERNAL_ALPR_API_KEY},
                timeout=settings.ALPR_TIMEOUT_SEC,
                follow_redirects=False,
            ) as client:
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
        if response.status_code != 200:
            # Do not expose upstream errors, credentials or internal URLs.
            raise HTTPException(502, "AI service trả về lỗi hoặc xác thực không thành công.")
        return response

    async def capture(self, payload: CaptureRequest) -> CaptureResult:
        request_id = uuid4().hex
        response = await self._request(
            "POST", "capture", json={**payload.model_dump(), "request_id": request_id}
        )
        try:
            result = CaptureResult.model_validate(response.json())
            if (result.request_id != request_id or result.lane != payload.lane
                    or result.session_id != payload.session_id):
                raise ValueError("Mismatched capture response")
        except (ValueError, ValidationError):
            raise HTTPException(502, "Kết quả AI service không hợp lệ.") from None
        result.image_url = f"/api/v1/alpr/images/{result.image_id}"
        return result

    async def image(self, image_id: str) -> bytes:
        response = await self._request("GET", f"images/{image_id}")
        if response.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise HTTPException(502, "AI service trả về ảnh không hợp lệ.")
        return response.content


alpr_service = ALPRService()
