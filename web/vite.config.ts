/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Keep stable framework/data/UI groups without splitting tiny packages into request-heavy chunks.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/')) return 'vendor-react';
          if (id.includes('@tanstack/react-query') || id.includes('/axios/')) return 'vendor-data';
          if (id.includes('framer-motion') || id.includes('@radix-ui') || id.includes('lucide-react')) return 'vendor-ui';
          if (id.includes('/jszip/') || id.includes('/file-saver/') || id.includes('/canvas-confetti/')) return 'vendor-export';
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
