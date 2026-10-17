import React from "react";
import Link from "next/link";
import { MessageSquare, Pin } from "lucide-react";
import { ConversationSummary } from "@/types/database";
import { cn } from "@/lib/utils";

interface ThreadListProps {
  threads: ConversationSummary[];
  activeId?: string;
}

export const ThreadList: React.FC<ThreadListProps> = ({ threads, activeId }) => {
  return (
    <div className="space-y-1 overflow-y-auto max-h-[calc(100vh-220px)]">
      {threads.map((thread) => {
        const isActive = thread.id === activeId;
        return (
          <Link
            key={thread.id}
            href={`/c/${thread.id}`}
            className={cn(
              "flex items-center justify-between p-2.5 rounded-lg text-sm transition-colors group",
              isActive ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 font-medium" : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            )}
          >
            <div className="flex items-center gap-2.5 truncate">
              <MessageSquare className="w-4 h-4 shrink-0 text-gray-500 group-hover:text-gray-400" />
              <span className="truncate">{thread.title}</span>
            </div>
            {thread.is_pinned && <Pin className="w-3.5 h-3.5 text-indigo-400 shrink-0" />}
          </Link>
        );
      })}
    </div>
  );
};
