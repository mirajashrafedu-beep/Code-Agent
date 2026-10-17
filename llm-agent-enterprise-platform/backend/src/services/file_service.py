import os
import io
import zipfile
from uuid import UUID
from database.s3_minio import get_s3_client, MINIO_BUCKET_UPLOADS, MINIO_BUCKET_EXPORTS

class FileService:
    def __init__(self):
        self.s3 = get_s3_client()

    async def create_presigned_upload(self, user_id: UUID, filename: str, content_type: str, directory_path: str = "") -> dict:
        prefix = f"{user_id}/{directory_path.strip('/')}".strip('/')
        file_key = f"{prefix}/{filename}" if prefix else f"{user_id}/{filename}"
        url = await self.s3.generate_presigned_upload_url(MINIO_BUCKET_UPLOADS, file_key)
        return {
            "upload_url": url,
            "file_key": file_key,
            "filename": filename
        }

    async def direct_upload(self, user_id: UUID, filename: str, file_bytes: bytes, content_type: str) -> dict:
        file_key = f"{user_id}/{filename}"
        await self.s3.upload_bytes(MINIO_BUCKET_UPLOADS, file_key, file_bytes, content_type)
        download_url = await self.s3.generate_presigned_download_url(MINIO_BUCKET_UPLOADS, file_key)
        return {
            "file_key": file_key,
            "filename": filename,
            "size_bytes": len(file_bytes),
            "bucket": MINIO_BUCKET_UPLOADS,
            "download_url": download_url
        }

    async def package_zip_export(self, user_id: UUID, file_keys: list[str], export_name: str = "export.zip") -> str:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for key in file_keys:
                try:
                    file_data = await self.s3.download_bytes(MINIO_BUCKET_UPLOADS, key)
                    arcname = key.split(f"{user_id}/")[-1]
                    zip_file.writestr(arcname, file_data)
                except Exception:
                    continue

        zip_buffer.seek(0)
        export_key = f"{user_id}/exports/{export_name}"
        await self.s3.upload_bytes(MINIO_BUCKET_EXPORTS, export_key, zip_buffer.getvalue(), "application/zip")
        return await self.s3.generate_presigned_download_url(MINIO_BUCKET_EXPORTS, export_key)
