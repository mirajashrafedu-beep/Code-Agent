"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiClient } from "@/lib/api-client";
import { Button } from "@/components/ui/button";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState("");

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await apiClient.post("/auth/register", { email, password, full_name: fullName });
      router.push("/auth/login");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Registration failed");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-[#0d1117]">
      <div className="bg-gray-900 border border-gray-800 rounded-xl max-w-sm w-full p-6 space-y-4 shadow-2xl">
        <h2 className="text-xl font-bold text-gray-100 text-center">Create Account</h2>
        {error && <div className="text-red-400 text-xs p-2 bg-red-950/30 rounded border border-red-800/40">{error}</div>}
        <form onSubmit={handleRegister} className="space-y-3">
          <div>
            <label className="text-xs text-gray-400 block mb-1">Full Name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <Button type="submit" className="w-full">Sign Up</Button>
        </form>
        <p className="text-center text-xs text-gray-400">
          Already have an account? <Link href="/auth/login" className="text-indigo-400 hover:underline">Sign In</Link>
        </p>
      </div>
    </div>
  );
}
