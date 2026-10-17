import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Enterprise Autonomous LLM Agent Platform",
  description: "Multi-Database Autonomous AI Agent Platform with Next.js 15, PostgreSQL, Qdrant, Redis, MongoDB, MinIO, and ClickHouse.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#0d1117] text-gray-100 antialiased min-h-screen">
        {children}
      </body>
    </html>
  );
}
