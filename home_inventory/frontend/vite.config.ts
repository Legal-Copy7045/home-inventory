import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The app is served under a per-session Ingress path
// (/api/hassio_ingress/<token>/), so every URL must be relative.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../server/home_inventory_server/static",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    proxy: { "/api": "http://localhost:8099" },
  },
  test: { environment: "node" },
});
