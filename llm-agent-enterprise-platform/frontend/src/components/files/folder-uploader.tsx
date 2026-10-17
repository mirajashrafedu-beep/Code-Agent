import React, { useRef } from "react";
import { Upload, X, FolderUp, CheckCircle2 } from "lucide-react";
import { useFileUpload } from "@/hooks/use-file-upload";
import { Button } from "@/components/ui/button";

interface FolderUploaderProps {
  isOpen: boolean;
  onClose: () => void;
}

export const FolderUploader: React.FC<FolderUploaderProps> = ({ isOpen, onClose }) => {
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const { uploadFiles, isUploading, progress } = useFileUpload();

  if (!isOpen) return null;

  const handleFolderSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      await uploadFiles(e.target.files);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-800 rounded-xl w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FolderUp className="w-5 h-5 text-indigo-400" />
            <h3 className="font-semibold text-gray-100">Upload Folder to MinIO & Vectorize</h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div
          onClick={() => folderInputRef.current?.click()}
          className="border-2 border-dashed border-gray-700 hover:border-indigo-500 rounded-xl p-8 flex flex-col items-center justify-center text-center cursor-pointer transition-colors bg-gray-950/50"
        >
          <Upload className="w-10 h-10 text-gray-500 mb-2" />
          <p className="text-sm font-medium text-gray-300">
            Click to select an entire folder
          </p>
          <p className="text-xs text-gray-500 mt-1">
            Documents will be uploaded directly to MinIO and indexed into Qdrant vector memory.
          </p>
          <input
            ref={folderInputRef}
            type="file"
            multiple
            // @ts-ignore
            webkitdirectory="true"
            directory=""
            className="hidden"
            onChange={handleFolderSelect}
          />
        </div>

        {isUploading && (
          <div className="space-y-2">
            <div className="flex justify-between text-xs text-gray-400">
              <span>Uploading & Vectorizing...</span>
              <span>{progress}%</span>
            </div>
            <div className="w-full bg-gray-800 rounded-full h-2">
              <div
                className="bg-indigo-600 h-2 rounded-full transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
};
