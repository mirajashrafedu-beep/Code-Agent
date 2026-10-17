import React from "react";
import { Database, FileText } from "lucide-react";
import { RAGSource } from "@/types/chat";

interface MemoryIndicatorProps {
  sources?: RAGSource[];
}

export const MemoryIndicator: React.FC<MemoryIndicatorProps> = ({ sources }) => {
  if (!sources || sources.length === 0) return null;

  return (
    <div className="mt-3 p-3 bg-gray-900/80 border border-indigo-500/30 rounded-lg text-xs">
      <div className="flex items-center gap-2 text-indigo-400 font-semibold mb-2">
        <Database className="w-4 h-4" />
        <span>Grounded Knowledge Base Sources ({sources.length})</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {sources.map((src, idx) => (
          <div key={idx} className="p-2 bg-gray-800/60 rounded border border-gray-700/60">
            <div className="flex items-center gap-1.5 text-gray-300 font-medium truncate">
              <FileText className="w-3.5 h-3.5 text-gray-400 shrink-0" />
              <span className="truncate">{src.filename || "Knowledge Doc"}</span>
            </div>
            <p className="mt-1 text-gray-400 line-clamp-2 text-[11px] leading-relaxed">
              "{src.text}"
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};
