/**
 * ManualDetectionDialog — in-map modal replacing the legacy `window.prompt`.
 *
 * Why: defense-hardened browser configurations block native prompts, which
 * locked operators out of manually adding targets. This dialog matches the
 * dark workstation theme and is keyboard-accessible (focus trap on input,
 * ESC and backdrop click cancel). Pattern mirrors ChangeDetectionDialog.tsx.
 */

import L from 'leaflet';
import { X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export type ManualDetectionDialogProps = {
  bounds: L.LatLngBounds | null;
  onConfirm: (objectClass: string) => void;
  onCancel: () => void;
};

export default function ManualDetectionDialog({
  bounds,
  onConfirm,
  onCancel,
}: ManualDetectionDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState('unknown');

  useEffect(() => {
    if (!bounds) return;
    setValue('unknown');
    const t = setTimeout(() => inputRef.current?.select(), 30);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      clearTimeout(t);
    };
  }, [bounds, onCancel]);

  if (!bounds) return null;

  const c = bounds.getCenter();

  const submit = () => {
    const cls = value.trim() || 'unknown';
    onConfirm(cls);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Classify manual detection"
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
              CLASSIFY MANUAL TARGET
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3, #7d92a8)' }}>
              {c.lat.toFixed(4)}, {c.lng.toFixed(4)}
            </div>
          </div>
          <button
            type="button"
            className="btn ghost icon xs"
            onClick={onCancel}
            aria-label="Cancel"
          >
            <X size={12} />
          </button>
        </header>

        <form
          onSubmit={(e) => { e.preventDefault(); submit(); }}
          style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}
        >
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3, #7d92a8)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
              Object class
            </span>
            <input
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="e.g. tank, frigate, building"
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

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" className="btn xs" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="btn xs primary">
              Confirm
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
