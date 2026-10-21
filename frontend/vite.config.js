import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [{ name: 'syntax-highlight', test: /[\\/]node_modules[\\/](?:highlight\.js|lowlight)[\\/]/ }],
        },
      },
    },
  },
});
