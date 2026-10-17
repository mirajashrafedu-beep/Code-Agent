export interface RAGSource {
  score: number;
  text: string;
  filename: string;
  chunk_index: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  token_count?: number;
  tool_calls?: any;
  rag_sources?: RAGSource[];
  created_at: string;
  isStreaming?: boolean;
  thought?: string;
}
