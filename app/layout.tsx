import type { Metadata } from "next";
import { Toaster } from "sonner";
import { Sidebar } from "@/components/sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Evolution OS",
  description: "Personal Growth Operating System",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="min-h-screen bg-background antialiased">
        <div className="flex">
          <Sidebar />
          <main className="flex-1 min-h-screen">
            {children}
          </main>
        </div>
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
