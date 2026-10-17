export interface User {
  id: string;
  email: string;
  full_name?: string;
  subscription_tier: string;
  is_active: boolean;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
  message_count?: number;
}
