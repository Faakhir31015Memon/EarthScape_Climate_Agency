import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const backend = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    server: {
      port: Number(env.VITE_PORT || 5173),
      host: true,
      proxy: {
        '/api': {
          target: backend,
          changeOrigin: true,
        },
      },
    },
    preview: { port: Number(env.VITE_PORT || 5173) },
    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          manualChunks: {
            charts: ['recharts'],
            maps: ['leaflet', 'react-leaflet'],
            vendor: ['react', 'react-dom', 'react-router-dom', 'axios'],
          },
        },
      },
    },
  }
})
