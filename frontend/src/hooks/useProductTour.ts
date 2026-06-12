/**
 * useProductTour — guided onboarding state for the Map workspace.
 *
 * Auto-opens the welcome modal on the operator's first visit (no
 * `sentinel:tour-completed` key in localStorage). Subsequent visits stay
 * silent; the tour is re-launchable from the Product Tour button in the
 * MapStage top-center toolbar.
 *
 * The localStorage try/catch fallback mirrors usePreferences.tsx — in
 * private-browsing mode persistence simply no-ops, which is acceptable.
 */

import { useCallback, useEffect, useState } from 'react';

import { TOUR_STEPS } from '../components/tour/tourSteps';

const LS_KEY = 'sentinel:tour-completed';

export type ProductTourState = {
  running: boolean;
  stepIndex: number;
  welcomeOpen: boolean;
  start: () => void;
  next: () => void;
  prev: () => void;
  finish: () => void;
  skip: () => void;
  dismissWelcome: () => void;
  launchFromButton: () => void;
};

export function useProductTour(): ProductTourState {
  const [running, setRunning] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [welcomeOpen, setWelcomeOpen] = useState(false);

  useEffect(() => {
    try {
      if (!localStorage.getItem(LS_KEY)) setWelcomeOpen(true);
    } catch {
      // private mode → no auto-open, manual launch still works
    }
  }, []);

  const markCompleted = () => {
    try { localStorage.setItem(LS_KEY, '1'); } catch { /* ignore */ }
  };

  const start = useCallback(() => {
    setWelcomeOpen(false);
    setStepIndex(0);
    setRunning(true);
  }, []);

  // Clamp at the last step — an index past the end unmounts the overlay
  // while `running` stays true (callers finish() at the boundary instead).
  const next = useCallback(() => setStepIndex((i) => Math.min(TOUR_STEPS.length - 1, i + 1)), []);
  const prev = useCallback(() => setStepIndex((i) => Math.max(0, i - 1)), []);

  const finish = useCallback(() => {
    setRunning(false);
    markCompleted();
  }, []);

  // Skip persists the same flag as Finish — the operator decided they don't
  // want the welcome modal again.
  const skip = finish;

  // "Maybe later" — close the modal but DO NOT set the flag, so the welcome
  // pops again next visit.
  const dismissWelcome = useCallback(() => setWelcomeOpen(false), []);

  const launchFromButton = useCallback(() => {
    setRunning(false);
    setStepIndex(0);
    setWelcomeOpen(true);
  }, []);

  return {
    running,
    stepIndex,
    welcomeOpen,
    start,
    next,
    prev,
    finish,
    skip,
    dismissWelcome,
    launchFromButton,
  };
}
