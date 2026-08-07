from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.upload import UploadResult
from app.services.upload_service import process_upload

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/{data_type}", response_model=UploadResult)
async def upload_data(data_type: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    batch = process_upload(db, data_type, file, raw)
    return UploadResult(
        id=batch.id,
        data_type=batch.data_type,
        file_name=batch.file_name,
        uploaded_at=batch.uploaded_at,
        row_count=batch.row_count,
        error_count=batch.error_count,
        status=batch.status,
        errors=getattr(batch, "errors", []),
    )
