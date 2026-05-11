import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import cesium from 'vite-plugin-cesium'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), cesium()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    watch: {
      usePolling: true,
    },
    proxy: {
      // Map `/basemap/{z}/{x}/{y}.png` to Carto's dark raster in local dev
      // so `npm run dev` outside Docker renders tiles without needing the
      // nginx fallback or pre-baked offline tiles.
      '/basemap': {
        target: 'https://a.basemaps.cartocdn.com',
        changeOrigin: true,
        secure: true,
        rewrite: (path: string) => path.replace(/^\/basemap/, '/dark_all'),
      },
    },
  }
})
