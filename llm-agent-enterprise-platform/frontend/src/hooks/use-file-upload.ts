import { useState } from "react";
import { apiClient } from "@/lib/api-client";
import { UploadedFile } from "@/types/files";

export function useFileUpload() {
  const [isUploading, setIsUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  const uploadFiles = async (files: FileList | File[]): Promise<UploadedFile[]> => {
    setIsUploading(true);
    setProgress(0);
    const uploadedList: UploadedFile[] = [];

    try {
      const total = files.length;
      for (let i = 0; i < total; i++) {
        const file = files[i];
        const formData = new FormData();
        formData.append("file", file);

        const res = await apiClient.post<UploadedFile>("/files/upload", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        uploadedList.push(res.data);
        setProgress(Math.round(((i + 1) / total) * 100));
      }
      return uploadedList;
    } finally {
      setIsUploading(false);
    }
  };

  return { uploadFiles, isUploading, progress };
}
