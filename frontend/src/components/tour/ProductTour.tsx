/**
 * ProductTour — welcome modal + spotlight + anchored tooltip.
 *
 * Renders three concerns in one component:
 *   1. Welcome modal (when state.welcomeOpen) — reuses the .confirm-overlay
 *      / .confirm-dialog markup from atoms.tsx so styling matches every
 *      other modal in the app.
 *   2. Spotlight backdrop (when state.running) — fixed-position overlay
 *      with an inverse-cutout (box-shadow trick) framing the current step's
 *      target element. pointer-events: none so the analyst can still
 *      interact with the highlighted control.
 *   3. Tooltip card — anchored next to the target via getBoundingClientRect.
 *      Falls back through preferred → bottom → top → right → left if the
 *      preferred placement would clip the viewport.
 *
 * Targets that don't exist (collapsed panels, unmounted SelectionPanel)
 * are auto-skipped — when stepIndex lands on one, the effect advances in
 * the operator's last-moved direction and React re-renders until a valid
 * target is reached or the end of the list, at which point the tour ends.
 */

import { useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { HelpCircle, X } from 'lucide-react';

import type { ProductTourState } from '../../hooks/useProductTour';
import { TOUR_STEPS, type Placement, type TourStep } from './tourSteps';

const CARD_W = 320;
const CARD_H = 180;
const PAD = 12;
const SPOTLIGHT_RADIUS = 6;

type Anchor = {
  card: { top: number; left: number };
  spotlight: { top: number; left: number; width: number; height: number };
  placement: Placement;
};

function pickPlacement(rect: DOMRect, preferred: Placement) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const candidates: Array<{ placement: Placement; top: number; left: number }> = [
    { placement: 'bottom', top: rect.bottom + PAD,                          left: rect.left + rect.width / 2 - CARD_W / 2 },
    { placement: 'top',    top: rect.top - CARD_H - PAD,                    left: rect.left + rect.width / 2 - CARD_W / 2 },
    { placement: 'right',  top: rect.top + rect.height / 2 - CARD_H / 2,    left: rect.right + PAD },
    { placement: 'left',   top: rect.top + rect.height / 2 - CARD_H / 2,    left: rect.left - CARD_W - PAD },
  ];

  const fits = (c: { top: number; left: number }) =>
    c.top >= PAD && c.left >= PAD && c.top + CARD_H <= vh - PAD && c.left + CARD_W <= vw - PAD;

  const ordered = [
    candidates.find((c) => c.placement === preferred)!,
    ...candidates.filter((c) => c.placement !== preferred),
  ];
  const chosen = ordered.find(fits) ?? ordered[0];

  return {
    placement: chosen.placement,
    top: Math.max(PAD, Math.min(chosen.top, vh - CARD_H - PAD)),
    left: Math.max(PAD, Math.min(chosen.left, vw - CARD_W - PAD)),
  };
}

function computeAnchor(step: TourStep, target: Element): Anchor {
  const rect = target.getBoundingClientRect();
  const placed = pickPlacement(rect, step.placement);
  return {
    card: { top: placed.top, left: placed.left },
    spotlight: {
      top: rect.top - SPOTLIGHT_RADIUS,
      left: rect.left - SPOTLIGHT_RADIUS,
      width: rect.width + SPOTLIGHT_RADIUS * 2,
      height: rect.height + SPOTLIGHT_RADIUS * 2,
    },
    placement: placed.placement,
  };
}

export default function ProductTour({
  state,
  onStepChange,
}: {
  state: ProductTourState;
  /** Fired with the *resolved* step id every time the spotlight lands on a
      new step (or null when the tour is not running). Lets the parent open
      panels, switch tabs, or otherwise satisfy prerequisites before the
      next render-pass tries to find the target. */
  onStepChange?: (stepId: string | null) => void;
}) {
  const {
    running, stepIndex, welcomeOpen,
    start, next, prev, finish, skip, dismissWelcome,
  } = state;

  // Last movement direction — used to keep advancing past missing targets in
  // the direction the operator was already going.
  const [moveDir, setMoveDir] = useState<1 | -1>(1);

  const currentStep: TourStep | null = useMemo(() => {
    if (!running) return null;
    if (stepIndex < 0 || stepIndex >= TOUR_STEPS.length) return null;
    return TOUR_STEPS[stepIndex];
  }, [running, stepIndex]);

  // Target existence is tri-state: null = still resolving (prep pending),
  // boolean = sampled. A plain render-time useMemo raced the parent's
  // onStepChange prep — the auto-skip effect saw `false` and advanced past
  // the first step of every prep-gated group (tab-*, tm-*, event-*, …)
  // before the panel the parent opened had committed to the DOM.
  const [targetExists, setTargetExists] = useState<boolean | null>(null);

  // Fire onStepChange whenever the parent needs to ready prerequisite state
  // for the upcoming step (open the right panel, switch a tab, …). Declared
  // BEFORE the sampling effect below so the parent's prep runs first within
  // the same commit.
  useEffect(() => {
    onStepChange?.(running && currentStep ? currentStep.id : null);
  }, [running, currentStep, onStepChange]);

  // Resolve target existence. If the target is already in the DOM, anchor
  // immediately; otherwise wait a frame + a macrotask so the panel opened by
  // onStepChange has rendered before we declare the target missing.
  useEffect(() => {
    if (!currentStep) { setTargetExists(null); return; }
    if (document.querySelector(currentStep.selector)) {
      setTargetExists(true);
      return;
    }
    setTargetExists(null);
    let timer: number | undefined;
    const raf = requestAnimationFrame(() => {
      timer = window.setTimeout(() => {
        setTargetExists(!!document.querySelector(currentStep.selector));
      }, 0);
    });
    return () => {
      cancelAnimationFrame(raf);
      if (timer != null) window.clearTimeout(timer);
    };
  }, [currentStep]);

  // Auto-skip missing targets — only once existence has resolved to `false`
  // (post-prep). Bounded by TOUR_STEPS.length — when we walk off the end of
  // the array in either direction we finish cleanly.
  useEffect(() => {
    if (!running || !currentStep) return;
    if (targetExists !== false) return;
    if (moveDir === 1) {
      if (stepIndex + 1 >= TOUR_STEPS.length) { finish(); return; }
      next();
    } else {
      if (stepIndex - 1 < 0) { finish(); return; }
      prev();
    }
  }, [running, currentStep, targetExists, moveDir, stepIndex, next, prev, finish]);

  // Compute card + spotlight position. Recompute on resize / scroll.
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  useLayoutEffect(() => {
    if (!currentStep || !targetExists) { setAnchor(null); return; }
    const target = document.querySelector(currentStep.selector);
    if (!target) return;
    setAnchor(computeAnchor(currentStep, target));

    const recompute = () => {
      const t = document.querySelector(currentStep.selector);
      if (t) setAnchor(computeAnchor(currentStep, t));
    };
    window.addEventListener('resize', recompute);
    window.addEventListener('scroll', recompute, true);
    return () => {
      window.removeEventListener('resize', recompute);
      window.removeEventListener('scroll', recompute, true);
    };
  }, [currentStep, targetExists]);

  // Tour-level keyboard: Esc skip, → next, ← prev.
  useEffect(() => {
    if (!running) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape')           { e.preventDefault(); skip(); }
      else if (e.key === 'ArrowRight')  {
        e.preventDefault();
        // On the last step → finish; next() would push stepIndex past the
        // end, unmounting the overlay while `running` stays true and this
        // handler keeps swallowing arrow keys app-wide.
        if (stepIndex >= TOUR_STEPS.length - 1) { finish(); return; }
        setMoveDir(1);
        next();
      }
      else if (e.key === 'ArrowLeft')   { e.preventDefault(); setMoveDir(-1); prev(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [running, stepIndex, next, prev, skip, finish]);

  // Welcome-modal keyboard: Esc = "Maybe later".
  useEffect(() => {
    if (!welcomeOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); dismissWelcome(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [welcomeOpen, dismissWelcome]);

  const isLast = currentStep ? stepIndex === TOUR_STEPS.length - 1 : false;

  return (
    <>
      {welcomeOpen && (
        <div role="presentation" className="confirm-overlay" onClick={dismissWelcome}>
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="product-tour-welcome-title"
            className="confirm-dialog"
            style={{ maxWidth: 460 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              id="product-tour-welcome-title"
              className="confirm-dialog-title"
              style={{ display: 'flex', alignItems: 'center', gap: 10 }}
            >
              <HelpCircle className="h-5 w-5" style={{ color: 'var(--accent)' }} />
              Welcome to Sentinel
            </div>
            <div className="confirm-dialog-body">
              You're looking at the Map workspace — the common operating picture for
              imagery, detections, asset tracks, and analytics. Take a quick tour to
              learn what each control does. You can re-launch it any time from the
              Product Tour button in the top toolbar.
            </div>
            <div className="confirm-dialog-actions">
              <button type="button" className="btn sm" onClick={dismissWelcome}>
                Maybe later
              </button>
              <button type="button" className="btn sm" onClick={skip}>
                Don't show again
              </button>
              <button type="button" className="btn sm primary" onClick={start} autoFocus>
                Take the tour
              </button>
            </div>
          </div>
        </div>
      )}

      {running && currentStep && anchor && (
        <div
          aria-hidden
          style={{ position: 'fixed', inset: 0, zIndex: 1000, pointerEvents: 'none' }}
        >
          {/* Spotlight: inverse-cutout via huge outset box-shadow. */}
          <div
            style={{
              position: 'fixed',
              top: anchor.spotlight.top,
              left: anchor.spotlight.left,
              width: anchor.spotlight.width,
              height: anchor.spotlight.height,
              borderRadius: 4,
              boxShadow: '0 0 0 9999px rgba(0,0,0,0.55), 0 0 0 2px var(--accent)',
              transition: 'top 120ms ease, left 120ms ease, width 120ms ease, height 120ms ease',
            }}
          />
          {/* Tooltip card. */}
          <div
            role="dialog"
            aria-modal="false"
            aria-labelledby="product-tour-step-title"
            style={{
              position: 'fixed',
              top: anchor.card.top,
              left: anchor.card.left,
              width: CARD_W,
              pointerEvents: 'auto',
              background: 'var(--bg-1)',
              border: '1px solid var(--accent)',
              borderRadius: 6,
              boxShadow: '0 12px 36px rgba(0,0,0,0.45)',
              padding: 14,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'start', gap: 8, marginBottom: 8 }}>
              <div style={{ flex: 1 }}>
                <div
                  id="product-tour-step-title"
                  style={{
                    fontFamily: 'var(--font-mono, monospace)',
                    fontSize: 11,
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                    color: 'var(--accent)',
                  }}
                >
                  {currentStep.title}
                </div>
                <div
                  style={{
                    fontFamily: 'var(--font-mono, monospace)',
                    fontSize: 10,
                    color: 'var(--muted)',
                    marginTop: 2,
                  }}
                >
                  Step {stepIndex + 1} of {TOUR_STEPS.length}
                </div>
              </div>
              <button
                type="button"
                onClick={skip}
                aria-label="Close tour"
                title="Close tour"
                style={{
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  color: 'var(--muted)', padding: 2,
                }}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div
              style={{
                fontSize: 13,
                lineHeight: 1.5,
                color: 'var(--text)',
                marginBottom: 14,
              }}
            >
              {currentStep.body}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" className="btn sm" onClick={skip}>Skip</button>
              <button
                type="button"
                className="btn sm"
                onClick={() => { setMoveDir(-1); prev(); }}
                disabled={stepIndex === 0}
              >
                Prev
              </button>
              {isLast ? (
                <button type="button" className="btn sm primary" onClick={finish} autoFocus>
                  Finish
                </button>
              ) : (
                <button
                  type="button"
                  className="btn sm primary"
                  onClick={() => { setMoveDir(1); next(); }}
                  autoFocus
                >
                  Next
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
