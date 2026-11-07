import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    server: {
      port: 5173, strictPort: true,
      proxy: {
        '/api': { target: env.CQA_BACKEND_URL || 'http://127.0.0.1:8000', changeOrigin: true },
        '/health': { target: env.CQA_BACKEND_URL || 'http://127.0.0.1:8000', changeOrigin: true },
      },
    },
  };
});
