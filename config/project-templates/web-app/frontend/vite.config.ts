import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

// Vite config for {{PROJECT_NAME}} frontend.
//
// `/api/*` is proxied to the FastAPI backend on the docker network.
// VITE_BACKEND_URL is set by docker-compose to http://backend:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
})
