import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { Bot, User, BrainCircuit } from "lucide-react";
import { ChatMessage } from "@/types/chat";
import { MemoryIndicator } from "./memory-indicator";
import { cn } from "@/lib/utils";

interface MessageBubbleProps {
  message: ChatMessage;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex gap-4 p-4 rounded-xl transition-colors", isUser ? "bg-gray-800/40" : "bg-gray-900/60 border border-gray-800/80")}>
      <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center shrink-0", isUser ? "bg-indigo-600 text-white" : "bg-gray-800 text-indigo-400 border border-indigo-500/20")}>
        {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
      </div>

      <div className="flex-1 overflow-hidden space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-400">
            {isUser ? "You" : "Enterprise Agent"}
          </span>
        </div>

        {message.thought && (
          <div className="flex items-center gap-2 text-xs text-amber-400/90 bg-amber-950/20 border border-amber-800/30 px-3 py-1.5 rounded-md">
            <BrainCircuit className="w-3.5 h-3.5 animate-pulse shrink-0" />
            <span className="italic">{message.thought}</span>
          </div>
        )}

        <div className="prose prose-invert max-w-none text-sm leading-relaxed text-gray-200">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[rehypeKatex]}
          >
            {message.content}
          </ReactMarkdown>
        </div>

        {message.isStreaming && (
          <span className="inline-block w-2 h-4 bg-indigo-500 animate-pulse align-middle ml-1" />
        )}

        <MemoryIndicator sources={message.rag_sources} />
      </div>
    </div>
  );
};
