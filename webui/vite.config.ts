import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Relative base so the SPA works under HA Supervisor ingress subpath
  // (e.g. /api/hassio_ingress/<token>/) where asset URLs must not resolve to root.
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8099",
        changeOrigin: true,
      },
    },
  },
});
