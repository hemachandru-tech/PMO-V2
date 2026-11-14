import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { port: 5173, strictPort: true },
  build: {
    sourcemap: true,
    rollupOptions: {
      output: {
        // React in its own long-cached chunk; ECharts lands in the chunk shared by the chart routes (US-134).
        manualChunks(id) {
          if (/node_modules\/\.pnpm\/(react|react-dom|react-router|react-router-dom|scheduler)@/.test(id)) return "react";
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
