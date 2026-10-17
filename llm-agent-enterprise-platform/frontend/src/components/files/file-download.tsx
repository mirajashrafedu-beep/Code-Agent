import React from "react";
import { Download, FileArchive } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api-client";

interface FileDownloadProps {
  fileKeys: string[];
}

export const FileDownloadTrigger: React.FC<FileDownloadProps> = ({ fileKeys }) => {
  const handleDownload = async () => {
    try {
      const res = await apiClient.post<{ download_url: string }>("/files/export-zip", fileKeys);
      if (res.data.download_url) {
        window.open(res.data.download_url, "_blank");
      }
    } catch (err) {
      console.error("Download trigger error:", err);
    }
  };

  return (
    <Button variant="secondary" size="sm" onClick={handleDownload} className="flex items-center gap-2">
      <FileArchive className="w-4 h-4 text-indigo-400" />
      <span>Download ZIP Package</span>
    </Button>
  );
};
