from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, UploadFile, File, Form
from src.api.deps import get_current_user
from src.schemas.files import S3PresignedUploadRequest, S3PresignedUploadResponse, FileUploadResponse
from src.services.file_service import FileService
from src.models.relational.user import User

router = APIRouter()

@router.post("/presigned-upload", response_model=S3PresignedUploadResponse)
async def get_presigned_upload(
    req: S3PresignedUploadRequest,
    current_user: User = Depends(get_current_user)
):
    service = FileService()
    res = await service.create_presigned_upload(
        user_id=current_user.id,
        filename=req.filename,
        content_type=req.content_type,
        directory_path=req.directory_path or ""
    )
    return res

@router.post("/upload", response_model=FileUploadResponse)
async def upload_file_direct(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    service = FileService()
    content = await file.read()
    res = await service.direct_upload(
        user_id=current_user.id,
        filename=file.filename or "uploaded_file",
        file_bytes=content,
        content_type=file.content_type or "application/octet-stream"
    )
    return res

@router.post("/export-zip")
async def export_files_zip(
    file_keys: List[str],
    current_user: User = Depends(get_current_user)
):
    service = FileService()
    download_url = await service.package_zip_export(
        user_id=current_user.id,
        file_keys=file_keys,
        export_name=f"export_{current_user.id}.zip"
    )
    return {"download_url": download_url}
