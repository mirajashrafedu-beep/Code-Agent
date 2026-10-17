import React, { useState } from "react";
import { Search, X, FileText, ArrowRight } from "lucide-react";
import { apiClient } from "@/lib/api-client";
import { Button } from "@/components/ui/button";

interface SearchModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const SearchModal: React.FC<SearchModalProps> = ({ isOpen, onClose }) => {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  if (!isOpen) return null;

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setIsSearching(true);
    try {
      const res = await apiClient.post("/memory/search", { query, top_k: 5 });
      setResults(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-800 rounded-xl w-full max-w-xl p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-100 flex items-center gap-2">
            <Search className="w-4 h-4 text-indigo-400" />
            Semantic Memory Vector Search
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search through past conversations and uploaded files..."
            className="flex-1 bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
          />
          <Button type="submit" size="sm" disabled={isSearching}>
            Search
          </Button>
        </form>

        <div className="space-y-2 max-h-80 overflow-y-auto">
          {results.map((r, idx) => (
            <div key={idx} className="p-3 bg-gray-950 rounded-lg border border-gray-800 space-y-1">
              <div className="flex items-center justify-between text-xs text-indigo-400 font-medium">
                <span>{r.filename}</span>
                <span>Score: {Math.round(r.score * 100)}%</span>
              </div>
              <p className="text-xs text-gray-300 leading-relaxed">{r.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
