import React, { useState, useRef, useEffect } from "react";
import { Send, Sparkles, FolderUp, Globe, Terminal, Database } from "lucide-react";
import { Button } from "@/components/ui/button";

interface InputBoxProps {
  onSend: (prompt: string, options: any) => void;
  disabled?: boolean;
  onOpenUpload?: () => void;
}

export const InputBox: React.FC<InputBoxProps> = ({ onSend, disabled, onOpenUpload }) => {
  const [prompt, setPrompt] = useState("");
  const [enableRag, setEnableRag] = useState(true);
  const [enableSandbox, setEnableSandbox] = useState(true);
  const [enableWebSearch, setEnableWebSearch] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [prompt]);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!prompt.trim() || disabled) return;

    onSend(prompt, { enableRag, enableSandbox, enableWebSearch });
    setPrompt("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="p-4 bg-gray-900 border-t border-gray-800">
      <div className="max-w-4xl mx-auto space-y-3">
        {/* Toggle options bar */}
        <div className="flex items-center justify-between text-xs text-gray-400 px-1">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setEnableRag(!enableRag)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border transition-colors ${enableRag ? "bg-indigo-950/60 border-indigo-600 text-indigo-300" : "bg-gray-800 border-gray-700 text-gray-500"}`}
            >
              <Database className="w-3.5 h-3.5" />
              <span>RAG Docs</span>
            </button>

            <button
              type="button"
              onClick={() => setEnableSandbox(!enableSandbox)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border transition-colors ${enableSandbox ? "bg-green-950/60 border-green-600 text-green-300" : "bg-gray-800 border-gray-700 text-gray-500"}`}
            >
              <Terminal className="w-3.5 h-3.5" />
              <span>Code Sandbox</span>
            </button>

            <button
              type="button"
              onClick={() => setEnableWebSearch(!enableWebSearch)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border transition-colors ${enableWebSearch ? "bg-blue-950/60 border-blue-600 text-blue-300" : "bg-gray-800 border-gray-700 text-gray-500"}`}
            >
              <Globe className="w-3.5 h-3.5" />
              <span>Web Search</span>
            </button>
          </div>

          {onOpenUpload && (
            <button
              type="button"
              onClick={onOpenUpload}
              className="flex items-center gap-1.5 hover:text-white transition-colors"
            >
              <FolderUp className="w-3.5 h-3.5" />
              <span>Upload Files / Folders</span>
            </button>
          )}
        </div>

        {/* Text Input Box */}
        <div className="relative flex items-end gap-2 bg-gray-950 border border-gray-800 rounded-xl p-2 focus-within:border-indigo-500 transition-colors">
          <textarea
            ref={textareaRef}
            rows={1}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question, run Python code, solve equations, or search uploaded docs..."
            className="w-full bg-transparent resize-none border-0 text-sm text-gray-100 placeholder-gray-500 focus:outline-none px-2 py-1 max-h-48"
            disabled={disabled}
          />
          <Button
            type="button"
            onClick={() => handleSubmit()}
            disabled={!prompt.trim() || disabled}
            size="sm"
            className="shrink-0"
          >
            <Send className="w-4 h-4" />
          </Button>
        </div>
      </div>
    </div>
  );
};
