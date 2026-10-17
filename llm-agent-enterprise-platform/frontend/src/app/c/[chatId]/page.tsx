"use client";

import React, { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "@/components/sidebar/sidebar";
import { ChatContainer } from "@/components/chat/chat-container";
import { InputBox } from "@/components/chat/input-box";
import { FolderUploader } from "@/components/files/folder-uploader";
import { useChatStream } from "@/hooks/use-chat-stream";
import { ConversationSummary } from "@/types/database";
import { apiClient } from "@/lib/api-client";

export default function ChatPage() {
  const params = useParams();
  const router = useRouter();
  const chatId = params?.chatId as string;
  const isNew = chatId === "new";

  const [threads, setThreads] = useState<ConversationSummary[]>([]);
  const [folderUploaderOpen, setFolderUploaderOpen] = useState(false);
  const { messages, setMessages, isStreaming, sendPrompt } = useChatStream(isNew ? undefined : chatId);

  useEffect(() => {
    // Load threads from Postgres
    apiClient
      .get("/conversations/")
      .then((res) => setThreads(res.data))
      .catch(() => {
        // Fallback demo threads
        setThreads([
          { id: "demo-1", title: "Quarterly Financial Analysis", is_pinned: true, created_at: "", updated_at: "" },
          { id: "demo-2", title: "Python Sandbox Code Run", is_pinned: false, created_at: "", updated_at: "" }
        ]);
      });

    // Load thread history if not new
    if (!isNew && chatId) {
      apiClient
        .get(`/conversations/${chatId}`)
        .then((res) => {
          if (res.data?.messages) {
            setMessages(res.data.messages);
          }
        })
        .catch(() => {});
    }
  }, [chatId, isNew]);

  return (
    <div className="flex h-screen overflow-hidden bg-[#0d1117]">
      <Sidebar
        threads={threads}
        activeId={isNew ? undefined : chatId}
        onNewChat={() => router.push("/c/new")}
      />

      <main className="flex-1 flex flex-col h-full overflow-hidden">
        <ChatContainer messages={messages} />
        <InputBox
          onSend={sendPrompt}
          disabled={isStreaming}
          onOpenUpload={() => setFolderUploaderOpen(true)}
        />
      </main>

      <FolderUploader
        isOpen={folderUploaderOpen}
        onClose={() => setFolderUploaderOpen(false)}
      />
    </div>
  );
}
