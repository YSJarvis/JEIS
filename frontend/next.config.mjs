/** Proxies /api/* to the Flask backend so the browser talks to one origin. */
const API = process.env.API_ORIGIN ?? "http://localhost:8000";
export default {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
