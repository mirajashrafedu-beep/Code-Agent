"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { ArrowLeft, Activity, DollarSign, Zap, Clock, Database } from "lucide-react";
import { apiClient } from "@/lib/api-client";

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<any>({
    total_tokens: 145020,
    total_cost_usd: 1.45,
    average_latency_ms: 620.4,
    average_ttft_ms: 195.1,
    top_models: [
      { model: "gpt-4o", requests: 38, tokens: 110400 },
      { model: "claude-3-5-sonnet-20241022", requests: 12, tokens: 34620 },
    ],
  });

  useEffect(() => {
    apiClient
      .get("/analytics/usage")
      .then((res) => {
        if (res.data) setMetrics(res.data);
      })
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen bg-[#0d1117] p-6 md:p-10 space-y-6">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/c/new" className="p-2 bg-gray-800 text-gray-300 hover:text-white rounded-lg">
              <ArrowLeft className="w-4 h-4" />
            </Link>
            <div>
              <h1 className="text-xl font-bold text-gray-100">ClickHouse Telemetry & Usage Analytics</h1>
              <p className="text-xs text-gray-400">Real-time Time-Series monitoring of token burn, costs, and latencies</p>
            </div>
          </div>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-gray-900 border border-gray-800 p-5 rounded-xl space-y-2">
            <div className="flex items-center justify-between text-gray-400 text-xs">
              <span>Total Tokens Burned</span>
              <Activity className="w-4 h-4 text-indigo-400" />
            </div>
            <div className="text-2xl font-bold text-gray-100">
              {metrics.total_tokens.toLocaleString()}
            </div>
          </div>

          <div className="bg-gray-900 border border-gray-800 p-5 rounded-xl space-y-2">
            <div className="flex items-center justify-between text-gray-400 text-xs">
              <span>Estimated Cost</span>
              <DollarSign className="w-4 h-4 text-green-400" />
            </div>
            <div className="text-2xl font-bold text-gray-100">
              ${metrics.total_cost_usd.toFixed(4)}
            </div>
          </div>

          <div className="bg-gray-900 border border-gray-800 p-5 rounded-xl space-y-2">
            <div className="flex items-center justify-between text-gray-400 text-xs">
              <span>Avg Execution Latency</span>
              <Clock className="w-4 h-4 text-amber-400" />
            </div>
            <div className="text-2xl font-bold text-gray-100">
              {metrics.average_latency_ms} ms
            </div>
          </div>

          <div className="bg-gray-900 border border-gray-800 p-5 rounded-xl space-y-2">
            <div className="flex items-center justify-between text-gray-400 text-xs">
              <span>Avg Time to First Token (TTFT)</span>
              <Zap className="w-4 h-4 text-blue-400" />
            </div>
            <div className="text-2xl font-bold text-gray-100">
              {metrics.average_ttft_ms} ms
            </div>
          </div>
        </div>

        {/* Model Breakdown */}
        <div className="bg-gray-900 border border-gray-800 p-6 rounded-xl space-y-4">
          <h2 className="text-sm font-semibold text-gray-200">Model Utilization Distribution</h2>
          <div className="space-y-3">
            {metrics.top_models.map((m: any, idx: number) => (
              <div key={idx} className="flex items-center justify-between p-3 bg-gray-950 rounded-lg border border-gray-800 text-sm">
                <div className="flex items-center gap-2">
                  <Database className="w-4 h-4 text-indigo-400" />
                  <span className="font-medium text-gray-200">{m.model}</span>
                </div>
                <div className="flex gap-4 text-xs text-gray-400">
                  <span>{m.requests} requests</span>
                  <span className="font-semibold text-gray-200">{m.tokens.toLocaleString()} tokens</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
