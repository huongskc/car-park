from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.tariff import Tariff
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.tariff_schema import TariffOut

router = APIRouter(
    prefix="/tariffs",
    tags=["Tariffs"],
)


@router.get(
    "",
    response_model=list[TariffOut],
    summary="Xem danh sách biểu phí gửi xe (VIEWER, OPERATOR, ADMIN)",
)
def list_tariffs(
    is_active: Optional[bool] = Query(default=None, description="Lọc theo trạng thái hiệu lực"),
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.VIEWER)),
) -> list[TariffOut]:
    """
    Endpoint tra cứu danh sách biểu phí gửi xe:
    - Quyền truy cập: VIEWER, OPERATOR, ADMIN.
    - Hỗ trợ lọc theo trạng thái hiệu lực `is_active` (True/False).
    - Hỗ trợ phân trang qua `skip` và `limit`.
    - Trả về danh sách biểu phí sắp xếp theo hiệu lực và thời điểm áp dụng mới nhất.
    """
    query = db.query(Tariff)

    if is_active is not None:
        query = query.filter(Tariff.is_active == is_active)

    return (
        query.order_by(Tariff.is_active.desc(), Tariff.effective_from.desc(), Tariff.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/{tariff_id}",
    response_model=TariffOut,
    summary="Tra cứu chi tiết biểu phí theo ID (VIEWER, OPERATOR, ADMIN)",
)
def get_tariff(
    tariff_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.VIEWER)),
) -> TariffOut:
    """
    Endpoint tra cứu thông tin chi tiết một biểu phí theo ID:
    - Quyền truy cập: VIEWER, OPERATOR, ADMIN.
    - Trả về 404 Not Found nếu không tìm thấy biểu phí.
    """
    tariff = db.query(Tariff).filter(Tariff.id == tariff_id).first()
    if not tariff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy biểu phí có mã ID '{tariff_id}'.",
        )
    return tariff
