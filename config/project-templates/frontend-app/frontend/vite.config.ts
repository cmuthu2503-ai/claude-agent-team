import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

// Vite config for {{PROJECT_NAME}}. Frontend-only — no /api proxy
// needed (add one back if you later add a backend service).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
  },
})
