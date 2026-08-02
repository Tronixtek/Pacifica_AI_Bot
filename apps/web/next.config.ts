import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@vtfx-mt5-bot/shared"],
  // Static export. The dashboard is a single client component that fetches
  // /api/bots at runtime, so there is nothing to render on a server. Exporting
  // it lets FastAPI serve the page from the same origin as the API, which
  // means one process and one port on the VPS instead of Node alongside
  // Python - and one port is the difference between an SSH tunnel being
  // trivial and being fiddly.
  output: "export",
  distDir: ".next",
  images: { unoptimized: true }
};

export default nextConfig;
