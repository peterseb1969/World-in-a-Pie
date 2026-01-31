import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// API targets: configurable via environment variables for container deployments
const defStoreTarget = process.env.VITE_DEF_STORE_TARGET || 'http://localhost:8002'
const templateStoreTarget = process.env.VITE_TEMPLATE_STORE_TARGET || 'http://localhost:8003'
const documentStoreTarget = process.env.VITE_DOCUMENT_STORE_TARGET || 'http://localhost:8004'
const dexTarget = process.env.VITE_DEX_TARGET || 'http://localhost:5556'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 3000,
    host: true,
    // Allow access from local network hostnames (e.g., raspberrypi.local)
    allowedHosts: true,
    proxy: {
      '/api/def-store': {
        target: defStoreTarget,
        changeOrigin: true
      },
      '/api/template-store': {
        target: templateStoreTarget,
        changeOrigin: true
      },
      '/api/document-store': {
        target: documentStoreTarget,
        changeOrigin: true
      },
      // Proxy Dex OIDC requests to avoid CORS issues
      '/dex': {
        target: dexTarget,
        changeOrigin: true
      }
    }
  }
})
