/** @type {import('next').NextConfig} */
const API = process.env.API_URL || "http://localhost:8000";

const nextConfig = {
  // Small self-contained server for Docker / Cloud Run: node .next/standalone/server.js
  output: "standalone",
  // The browser calls /api/... on the website itself; Next.js forwards it to FastAPI.
  // So download links and the "Get documents" button work without exposing the API address.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
export default nextConfig;
