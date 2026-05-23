/**
 * RangeRingsDialog — operator inputs a comma-separated list of radii (km)
 * after picking a center on the map. Mirrors the ManualDetectionDialog
 * pattern. Used for SAM envelope rings, artillery radii, sensor reach.
 */

import { X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export type RangeRingsDialogProps = {
  center: { lat: number; lon: number } | null;
  defaultRadiiKm?: string;
  onConfirm: (radiiKm: number[]) => void;
  onCancel: () => void;
};

export default function RangeRingsDialog({
  center,
  defaultRadiiKm = '5, 10, 20',
  onConfirm,
  onCancel,
}: RangeRingsDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(defaultRadiiKm);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!center) return;
    setValue(defaultRadiiKm);
    setError(null);
    const t = setTimeout(() => inputRef.current?.select(), 30);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
    };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); clearTimeout(t); };
  }, [center, defaultRadiiKm, onCancel]);

  if (!center) return null;

  const submit = () => {
    const parts = value.split(/[,\s]+/).map((p) => p.trim()).filter(Boolean);
    const radii: number[] = [];
    for (const p of parts) {
      const n = Number(p);
      if (!Number.isFinite(n) || n <= 0) {
        setError(`Invalid radius: "${p}"`);
        return;
      }
      radii.push(n);
    }
    if (!radii.length) {
      setError('Provide at least one radius (km).');
      return;
    }
    onConfirm(radii);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Range rings"
      style={{
        position: 'fixed', inset: 0, zIndex: 1800,
        display: 'grid', placeItems: 'center',
        background: 'color-mix(in oklab, var(--bg-0) 60%, transparent)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(420px, calc(100vw - 32px))',
          background: 'var(--bg-1)',
          border: '1px solid var(--line)',
          borderRadius: 12,
          boxShadow: '0 24px 48px rgba(0,0,0,.55)',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
        }}
      >
        <header style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '14px 16px', borderBottom: '1px solid var(--line)',
        }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '.04em' }}>
              RANGE RINGS
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3, #7d92a8)' }}>
              {center.lat.toFixed(4)}, {center.lon.toFixed(4)}
            </div>
          </div>
          <button type="button" className="btn ghost icon xs" onClick={onCancel} aria-label="Cancel">
            <X size={12} />
          </button>
        </header>

        <form
          onSubmit={(e) => { e.preventDefault(); submit(); }}
          style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}
        >
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3, #7d92a8)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
              Radii (km, comma-separated)
            </span>
            <input
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => { setValue(e.target.value); setError(null); }}
              placeholder="e.g. 5, 10, 20"
              autoFocus
              spellCheck={false}
              style={{
                background: 'var(--bg-0, #0e1620)',
                border: '1px solid var(--line-2, #233241)',
                color: 'var(--ink-0, #d7e3f1)',
                fontFamily: 'var(--font-mono)',
                fontSize: 13,
                padding: '8px 10px',
                outline: 'none',
              }}
            />
          </label>
          {error && (
            <div style={{ fontSize: 11, color: 'var(--color-sentinel-crit, #ff5577)' }}>
              {error}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" className="btn xs" onClick={onCancel}>Cancel</button>
            <button type="submit" className="btn xs primary">Place rings</button>
          </div>
        </form>
      </div>
    </div>
  );
}
