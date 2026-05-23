/**
 * SwipeControl — side-by-side imagery comparator.
 *
 * Renders a second TileLayer in a dedicated Leaflet pane and clips the pane
 * with CSS `clip-path: inset(...)` driven by a draggable vertical divider.
 * No external Leaflet plugin is required — the implementation is pure
 * react-leaflet + CSS, keeping the workstation fully offline-safe.
 *
 * Usage: render alongside the primary TileLayer; pass the COG URL for the
 * comparison pass. The divider snaps to viewport horizontal position.
 */

import { useEffect, useRef, useState } from 'react';
import { TileLayer, useMap } from 'react-leaflet';

const PANE_NAME = 'sentinel-compare';

export type SwipeControlProps = {
  url: string;
  maxNativeZoom?: number;
  /** Label for the divider chip (e.g. "Pass 42"). */
  label?: string;
  onClose: () => void;
};

export default function SwipeControl({
  url,
  maxNativeZoom,
  label,
  onClose,
}: SwipeControlProps) {
  const map = useMap();
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Horizontal divider position as fraction of map container width [0..1].
  const [frac, setFrac] = useState(0.5);
  const [dragging, setDragging] = useState(false);

  // Create a dedicated pane once. zIndex above primary imagery (200) and
  // below cartographic overlay (300) so it competes only with imagery.
  useEffect(() => {
    if (!map) return;
    let pane = map.getPane(PANE_NAME);
    if (!pane) {
      pane = map.createPane(PANE_NAME);
      pane.style.zIndex = '250';
      pane.style.pointerEvents = 'none';
    }
    return () => {
      const p = map.getPane(PANE_NAME);
      if (p) p.style.clipPath = '';
    };
  }, [map]);

  // Apply the clip on every frame change.
  useEffect(() => {
    const pane = map.getPane(PANE_NAME);
    if (!pane) return;
    const pct = Math.max(0, Math.min(1, frac)) * 100;
    pane.style.clipPath = `inset(0 0 0 ${pct}%)`;
  }, [map, frac]);

  // Track pointer drag on the divider.
  useEffect(() => {
    if (!dragging) return;
    const mapEl = map.getContainer();
    const move = (e: PointerEvent) => {
      const rect = mapEl.getBoundingClientRect();
      const x = e.clientX - rect.left;
      setFrac(Math.max(0.02, Math.min(0.98, x / rect.width)));
    };
    const up = () => setDragging(false);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [dragging, map]);

  // Position the divider chip absolutely over the map container.
  useEffect(() => {
    const mapEl = map.getContainer();
    if (!containerRef.current) return;
    const el = containerRef.current;
    el.style.position = 'absolute';
    el.style.top = '0';
    el.style.bottom = '0';
    el.style.left = '0';
    el.style.right = '0';
    el.style.pointerEvents = 'none';
    el.style.zIndex = '450';
    mapEl.parentElement?.appendChild(el);
    return () => {
      el.parentNode?.removeChild(el);
    };
  }, [map]);

  return (
    <>
      <TileLayer
        url={url}
        pane={PANE_NAME}
        maxZoom={22}
        maxNativeZoom={maxNativeZoom ?? 18}
        keepBuffer={6}
        updateWhenZooming={false}
      />
      <div ref={containerRef}>
        <div
          style={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            left: `${frac * 100}%`,
            transform: 'translateX(-50%)',
            width: 32,
            cursor: 'ew-resize',
            pointerEvents: 'auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
          onPointerDown={(e) => { e.preventDefault(); setDragging(true); }}
        >
          <div
            style={{
              width: 2,
              flex: 1,
              background: 'var(--accent, #ff7a1a)',
              boxShadow: '0 0 8px var(--accent, #ff7a1a)',
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: '50%',
              transform: 'translateY(-50%)',
              padding: '6px 8px',
              background: 'var(--bg-1, #0e1620)',
              border: '1px solid var(--accent, #ff7a1a)',
              color: 'var(--accent, #ff7a1a)',
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '.08em',
              textTransform: 'uppercase',
              whiteSpace: 'nowrap',
              userSelect: 'none',
            }}
          >
            ⇆ {label || 'compare'}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{
            position: 'absolute',
            top: 10,
            left: 'calc(50% + 24px)',
            transform: 'translateX(0)',
            padding: '4px 8px',
            background: 'var(--bg-1, #0e1620)',
            border: '1px solid var(--line, #2b3a4d)',
            color: 'var(--ink-1, #d7e3f1)',
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            cursor: 'pointer',
            pointerEvents: 'auto',
          }}
        >
          Exit compare
        </button>
      </div>
    </>
  );
}

// Hint: a custom Leaflet pane named ``sentinel-compare`` is created on mount
// and cleaned up on unmount. The pane's `clip-path` is updated each render.
const _PANE_NAME = PANE_NAME;
void _PANE_NAME;
