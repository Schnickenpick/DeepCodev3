import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Electron loads from a file:// path in production, so use relative base.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5173, strictPort: true },
  build: { outDir: "dist", emptyOutDir: true },
});
