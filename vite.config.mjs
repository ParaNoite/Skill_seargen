import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
  build: {
    lib: {
      entry: resolve("frontend/nav.jsx"),
      formats: ["es"],
      fileName: () => "react-nav.js",
    },
    outDir: "src/skill_gather/web_assets",
    emptyOutDir: false,
  },
});
