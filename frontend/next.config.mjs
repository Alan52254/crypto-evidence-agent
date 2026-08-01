import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const nextConfig = {
  devIndicators: false,
  outputFileTracingRoot: fileURLToPath(new URL(".", import.meta.url)),
  serverExternalPackages: [],
  experimental: {
    proxyTimeout: 600_000, // 10 minutes for SSE streams
  },
};

export default nextConfig;
