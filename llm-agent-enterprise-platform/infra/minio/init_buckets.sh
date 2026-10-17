#!/bin/sh
set -e

echo "Configuring MinIO client alias..."
/usr/bin/mc alias set myminio http://minio:9000 "${MINIO_ROOT_USER:-minio_admin}" "${MINIO_ROOT_PASSWORD:-minio_secure_password}"

echo "Creating buckets..."
/usr/bin/mc mb --ignore-existing myminio/user-uploads
/usr/bin/mc mb --ignore-existing myminio/exports

echo "Setting bucket policies..."
/usr/bin/mc anonymous set download myminio/user-uploads
/usr/bin/mc anonymous set download myminio/exports

echo "MinIO initialization completed successfully."
exit 0
