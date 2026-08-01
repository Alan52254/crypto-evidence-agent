import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const nextConfig = {
  devIndicators: false,
  outputFileTracingRoot: fileURLToPath(new URL(".", import.meta.url)),
  // 延長 serverless function timeout（分析回合最多 15 分鐘，與 route.ts 的 maxDuration=900 一致）
  serverExternalPackages: [],
  experimental: {
    proxyTimeout: 900_000, // 15 分鐘 SSE proxy timeout
  },
};

export default nextConfig;
