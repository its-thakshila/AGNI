import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// GitHub Pages serves the site from /<repo-name>/, so production builds for
// Pages set VITE_BASE=/AGNI/ (see .github/workflows/deploy-frontend.yml).
export default defineConfig({
  base: process.env.VITE_BASE ?? '/',
  plugins: [react(), tailwindcss()],
  worker: { format: "es" },
});
