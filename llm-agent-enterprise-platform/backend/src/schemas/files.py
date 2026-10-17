from pydantic import BaseModel
from typing import List, Optional

class S3PresignedUploadRequest(BaseModel):
    filename: str
    content_type: str
    directory_path: Optional[str] = ""

class S3PresignedUploadResponse(BaseModel):
    upload_url: str
    file_key: str
    filename: str

class FileUploadResponse(BaseModel):
    file_key: str
    filename: str
    size_bytes: int
    bucket: str
    download_url: Optional[str] = None
