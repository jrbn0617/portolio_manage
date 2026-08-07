from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UploadResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    data_type: str
    file_name: str
    uploaded_at: datetime
    row_count: int
    error_count: int
    status: str
    errors: list[str] = []
