import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The app came from Create React App; its source files were renamed .js -> .jsx (JSX needs the .jsx name in Vite 8).
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: { port: 5173 },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/setupTests.jsx"] },
});
