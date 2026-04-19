/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      // Proxy API calls to the .NET backend-dotnet service
      { source: "/api/:path*", destination: process.env.BACKEND_URL
          ? `${process.env.BACKEND_URL}/:path*`
          : "http://localhost:5000/:path*" },
    ];
  },
};
module.exports = nextConfig;
