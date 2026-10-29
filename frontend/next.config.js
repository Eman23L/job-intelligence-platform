/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async redirects() {
    // Old pages folded into Find jobs.
    return [
      { source: "/linkedin-indeed", destination: "/find", permanent: false },
      { source: "/daily-search", destination: "/find", permanent: false }
    ];
  }
};

module.exports = nextConfig;
