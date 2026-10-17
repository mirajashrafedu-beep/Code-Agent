import { useState, useCallback } from "react";
import { ChatMessage, RAGSource } from "@/types/chat";

export function useChatStream(conversationId?: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  const sendPrompt = useCallback(
    async (
      promptText: string,
      options?: {
        modelName?: string;
        enableRag?: boolean;
        enableSandbox?: boolean;
        enableWebSearch?: boolean;
      }
    ) => {
      const userMsg: ChatMessage = {
        id: "usr-" + Date.now(),
        role: "user",
        content: promptText,
        created_at: new Date().toISOString(),
      };

      const assistantMsgId = "ast-" + Date.now();
      const initialAssistantMsg: ChatMessage = {
        id: assistantMsgId,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        isStreaming: true,
      };

      setMessages((prev) => [...prev, userMsg, initialAssistantMsg]);
      setIsStreaming(true);

      const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
      const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

      try {
        const response = await fetch(`${API_URL}/chat/stream`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: token ? `Bearer ${token}` : "",
          },
          body: JSON.stringify({
            conversation_id: conversationId,
            prompt: promptText,
            model_name: options?.modelName || "gpt-4o",
            enable_rag: options?.enableRag ?? true,
            enable_sandbox: options?.enableSandbox ?? true,
            enable_web_search: options?.enableWebSearch ?? true,
          }),
        });

        if (!response.body) throw new Error("ReadableStream not available");

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          let currentEvent = "message";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.replace("event: ", "").trim();
            } else if (line.startsWith("data: ")) {
              const rawData = line.replace("data: ", "").trim();
              if (!rawData) continue;
              try {
                const parsed = JSON.parse(rawData);

                setMessages((prev) =>
                  prev.map((msg) => {
                    if (msg.id !== assistantMsgId) return msg;

                    if (currentEvent === "token") {
                      return { ...msg, content: msg.content + (parsed.token || "") };
                    }
                    if (currentEvent === "thought") {
                      return { ...msg, thought: parsed.thought };
                    }
                    if (currentEvent === "sources") {
                      return { ...msg, rag_sources: parsed.sources as RAGSource[] };
                    }
                    if (currentEvent === "done") {
                      return { ...msg, isStreaming: false };
                    }
                    return msg;
                  })
                );
              } catch (e) {
                console.error("Parse SSE data error", e);
              }
            }
          }
        }
      } catch (err) {
        console.error("Streaming error:", err);
      } finally {
        setIsStreaming(false);
        setMessages((prev) =>
          prev.map((msg) => (msg.id === assistantMsgId ? { ...msg, isStreaming: false } : msg))
        );
      }
    },
    [conversationId]
  );

  return { messages, setMessages, isStreaming, sendPrompt };
}
