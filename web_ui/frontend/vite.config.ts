import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建到 ../dist，由 Python server 托管；base 用相对路径便于静态托管
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
