import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// API targets: configurable via environment variables for container deployments
const defStoreTarget = process.env.VITE_DEF_STORE_TARGET || 'http://localhost:8002'
const templateStoreTarget = process.env.VITE_TEMPLATE_STORE_TARGET || 'http://localhost:8003'

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
    proxy: {
      '/api/def-store': {
        target: defStoreTarget,
        changeOrigin: true
      },
      '/api/template-store': {
        target: templateStoreTarget,
        changeOrigin: true
      }
    }
  }
})
