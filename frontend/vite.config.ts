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
      // Dev-only convenience: when running `npm run dev` OUTSIDE the
      // docker compose stack, forward `/basemap/{z}/{x}/{y}.png` to Carto
      // so devs without the pre-baked tile pyramid still see a map.
      //
      // This block is read only by Vite's dev server; the production
      // build (vite build → static bundle served by sentinel-nginx) never
      // touches it. In a docker compose dev loop, basemap tiles come from
      // the sentinel-assets container via the reverse proxy and this
      // entry is bypassed.
      '/basemap': {
        target: 'https://a.basemaps.cartocdn.com',
        changeOrigin: true,
        secure: true,
        rewrite: (path: string) => path.replace(/^\/basemap/, '/dark_all'),
      },
    },
  }
})
