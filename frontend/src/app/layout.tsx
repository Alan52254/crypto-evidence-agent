import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AlphaSonar AI — HOYA BIT",
  description:
    "AI-powered crypto market intelligence terminal. Auditable evidence-based analysis with real-time ReAct agent tracing.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-Hant" className="light" suppressHydrationWarning>
      <body className="overflow-hidden" suppressHydrationWarning>{children}</body>
    </html>
  );
}
