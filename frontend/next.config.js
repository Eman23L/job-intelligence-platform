/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async redirects() {
    // The LinkedIn & Indeed page became the Daily search page.
    return [{ source: "/linkedin-indeed", destination: "/daily-search", permanent: true }];
  }
};

module.exports = nextConfig;
