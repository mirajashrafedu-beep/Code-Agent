export interface UploadedFile {
  file_key: string;
  filename: string;
  size_bytes: number;
  bucket: string;
  download_url?: string;
}
