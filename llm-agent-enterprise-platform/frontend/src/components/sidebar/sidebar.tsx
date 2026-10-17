import React, { useState } from "react";
import Link from "next/link";
import { Plus, BarChart3, Search, Database, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThreadList } from "./thread-list";
import { SearchModal } from "./search-modal";
import { ConversationSummary } from "@/types/database";

interface SidebarProps {
  threads: ConversationSummary[];
  activeId?: string;
  onNewChat: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ threads, activeId, onNewChat }) => {
  const [searchOpen, setSearchOpen] = useState(false);

  return (
    <aside className="w-64 bg-gray-950 border-r border-gray-800 flex flex-col justify-between p-3 shrink-0">
      <div className="space-y-4">
        {/* Logo / Header */}
        <div className="flex items-center gap-2.5 px-2 py-1">
          <div className="w-7 h-7 bg-indigo-600 rounded-lg flex items-center justify-center font-bold text-white text-sm shadow-md">
            <Layers className="w-4 h-4" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-gray-100">Enterprise AI</h1>
            <p className="text-[10px] text-gray-400">6-Engine Agent Platform</p>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="space-y-1.5">
          <Button onClick={onNewChat} className="w-full justify-start gap-2 text-sm" size="sm">
            <Plus className="w-4 h-4" />
            <span>New Chat</span>
          </Button>

          <Button
            variant="secondary"
            onClick={() => setSearchOpen(true)}
            className="w-full justify-start gap-2 text-sm"
            size="sm"
          >
            <Search className="w-4 h-4 text-gray-400" />
            <span>Semantic Search</span>
          </Button>
        </div>

        {/* Thread History */}
        <div className="pt-2">
          <div className="text-[11px] font-semibold text-gray-500 uppercase px-2 mb-2 tracking-wider">
            Conversations
          </div>
          <ThreadList threads={threads} activeId={activeId} />
        </div>
      </div>

      {/* Bottom telemetry link */}
      <div className="pt-3 border-t border-gray-800">
        <Link
          href="/analytics"
          className="flex items-center gap-2.5 p-2 rounded-lg text-sm text-gray-400 hover:text-gray-200 hover:bg-gray-800/60 transition-colors"
        >
          <BarChart3 className="w-4 h-4 text-indigo-400" />
          <span>ClickHouse Analytics</span>
        </Link>
      </div>

      <SearchModal isOpen={searchOpen} onClose={() => setSearchOpen(false)} />
    </aside>
  );
};
