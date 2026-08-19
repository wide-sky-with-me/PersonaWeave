import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL('../src/personaweave/static', import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/node_modules/antd/') || id.includes('/node_modules/@ant-design/')) return 'antd'
          if (id.includes('/node_modules/motion/') || id.includes('/node_modules/framer-motion/')) return 'motion'
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/')) return 'react'
          return 'vendor'
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8100',
    },
  },
})
