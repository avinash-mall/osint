import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import cesium from 'vite-plugin-cesium'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), cesium()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          // leaflet.vectorgrid is a UMD plugin needing a global `L`; keep it in
          // its OWN chunk so the dynamic import in DetectionTileLayer stays lazy
          // (loaded after window.L is set) instead of being lumped into the
          // eager vendor-misc chunk — see why-detection-mvt-tiles.md.
          if (id.includes('leaflet.vectorgrid')) return 'vendor-vectorgrid'
          if (id.includes('/react/') || id.includes('/react-dom/')) return 'vendor-react'
          if (id.includes('/leaflet/') || id.includes('/react-leaflet/')) return 'vendor-map'
          if (id.includes('/three/') || id.includes('/react-force-graph-2d/') || id.includes('/react-globe.gl/') || id.includes('/cesium/')) {
            return 'vendor-graph'
          }
          if (id.includes('/hls.js/')) return 'vendor-video'
          return 'vendor-misc'
        },
      },
    },
  },
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
