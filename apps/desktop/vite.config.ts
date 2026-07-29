import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite configuration.
 *
 * `base: "./"` matters for the packaged app: Electron loads the bundle over
 * `file://`, where absolute asset paths resolve against the filesystem root and
 * every asset 404s.
 *
 * The dev server proxies `/api` to the Python backend so the browser and the
 * Electron renderer use the same relative URLs, and so the SSE stream is not a
 * cross-origin request during development.
 */
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${process.env.PC_PORT || 8765}`,
        changeOrigin: true,
        // Server-sent events must not be buffered by the proxy.
        ws: false,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (String(proxyRes.headers["content-type"]).includes("text/event-stream")) {
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // three.js and CodeMirror are large and change rarely; splitting them
        // keeps the app chunk small enough to reload quickly during development.
        manualChunks: {
          three: ["three"],
          codemirror: [
            "@codemirror/state",
            "@codemirror/view",
            "@codemirror/commands",
            "@codemirror/language",
            "@codemirror/lang-markdown",
            "@codemirror/theme-one-dark",
            "@codemirror/search",
          ],
        },
      },
    },
  },
});
