/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Stable vendor chunks: these change far less often than app code, so a UI
// rebuild leaves their hashed files (and browser caches) untouched.
const VENDOR_CHUNKS: Array<[RegExp, string]> = [
  [/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/, "vendor-react"],
  [/[\\/]node_modules[\\/]@mantine[\\/]/, "vendor-mantine"],
  [/[\\/]node_modules[\\/]@tabler[\\/]icons-react[\\/]/, "vendor-icons"],
];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  test: {
    // Speeds up Floating UI under jsdom; see the file.
    setupFiles: ["./src/test_setup.ts"],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          for (const [pattern, name] of VENDOR_CHUNKS) {
            if (pattern.test(id)) {
              return name;
            }
          }
          return undefined;
        },
      },
    },
  },
});
