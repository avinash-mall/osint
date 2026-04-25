import { useEffect, useRef, useState } from 'react';

// Tell CesiumJS where to find its vendored workers/assets
(window as Window & { CESIUM_BASE_URL?: string }).CESIUM_BASE_URL = '/cesium/';

export default function View3D({ fmvClipId: _fmvClipId }: { fmvClipId?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    let viewer: { destroy?: () => void } | null = null;

    import('cesium').then(({ Viewer, Ion, createWorldImageryAsync, TileMapServiceImageryProvider, buildModuleUrl }) => {
      try {
        // Disable Ion telemetry — we serve assets locally
        Ion.defaultAccessToken = '';

        // Try offline TMS basemap first; fall back to no imagery
        let imageryProvider;
        try {
          imageryProvider = new TileMapServiceImageryProvider({
            url: buildModuleUrl('Assets/Textures/NaturalEarthII'),
          });
        } catch {
          imageryProvider = false as unknown as undefined;
        }

        viewer = new Viewer(containerRef.current!, {
          imageryProvider: imageryProvider as ReturnType<typeof createWorldImageryAsync> | undefined,
          baseLayerPicker: false,
          geocoder: false,
          homeButton: false,
          sceneModePicker: false,
          navigationHelpButton: false,
          animation: false,
          timeline: false,
          fullscreenButton: false,
          infoBox: false,
          selectionIndicator: false,
          creditContainer: document.createElement('div'), // hide credit overlay
        });

        viewerRef.current = viewer;
        setLoading(false);
      } catch (err) {
        setError(String(err));
        setLoading(false);
      }
    }).catch(err => {
      setError(`Failed to load CesiumJS: ${err}`);
      setLoading(false);
    });

    return () => {
      if (viewer && typeof (viewer as { destroy?: () => void }).destroy === 'function') {
        (viewer as { destroy: () => void }).destroy();
      }
      viewerRef.current = null;
    };
  }, []);

  return (
    <div className="relative w-full h-full bg-slate-950">
      <div ref={containerRef} className="w-full h-full" />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center text-slate-400 text-sm">
          Loading 3D globe…
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center text-red-400 text-sm px-4 text-center">
          {error}
        </div>
      )}
    </div>
  );
}
