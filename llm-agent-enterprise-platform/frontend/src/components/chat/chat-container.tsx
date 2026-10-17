import React from "react";
import { ChatMessage } from "@/types/chat";
import { MessageBubble } from "./message-bubble";
import { useAutoScroll } from "@/hooks/use-auto-scroll";

interface ChatContainerProps {
  messages: ChatMessage[];
}

export const ChatContainer: React.FC<ChatContainerProps> = ({ messages }) => {
  const containerRef = useAutoScroll([messages]);

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto p-4 md:p-6 space-y-4 max-w-4xl mx-auto w-full"
    >
      {messages.length === 0 ? (
        <div className="h-full flex flex-col items-center justify-center text-center space-y-3 my-auto py-24">
          <div className="w-12 h-12 bg-indigo-600/20 text-indigo-400 rounded-2xl flex items-center justify-center border border-indigo-500/30">
            <span className="text-xl font-bold">⚡</span>
          </div>
          <h2 className="text-xl font-semibold text-gray-200">
            Enterprise Autonomous Agent
          </h2>
          <p className="text-sm text-gray-400 max-w-md">
            Powered by 6 database engines (PostgreSQL, Qdrant, Redis, MongoDB, MinIO, ClickHouse) with sandboxed execution and RAG.
          </p>
        </div>
      ) : (
        messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)
      )}
    </div>
  );
};
