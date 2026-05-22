/**
 * usePreferences — operator UI preferences (theme · density · clock TZ).
 *
 * The CSS already ships `.theme-dark` / `.theme-light` / `.dens-compact` /
 * `.dens-comfort` but nothing toggled them (UX-AUDIT F18). This provider is
 * the single source of truth: it persists each choice to `localStorage`,
 * re-applies the matching `<html>` classes on every change (and on mount),
 * and exposes setters consumed by the analyst menu (F10) and the analytics
 * toolbar density check (F16).
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react';

export type Theme = 'dark' | 'light';
export type Density = 'compact' | 'comfort';
export type ClockTz = 'utc' | 'local';

type Preferences = {
  theme: Theme;
  density: Density;
  clockTz: ClockTz;
  setTheme: (t: Theme) => void;
  setDensity: (d: Density) => void;
  setClockTz: (c: ClockTz) => void;
};

const LS = {
  theme: 'sentinel:theme',
  density: 'sentinel:density',
  clockTz: 'sentinel:clockTz',
} as const;

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // localStorage can be unavailable (private mode / quota) — preferences
    // then simply do not persist across reloads, which is acceptable.
  }
}

const PrefsContext = createContext<Preferences | null>(null);

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => read(LS.theme, ['dark', 'light'] as const, 'dark'));
  const [density, setDensityState] = useState<Density>(() => read(LS.density, ['compact', 'comfort'] as const, 'comfort'));
  const [clockTz, setClockTzState] = useState<ClockTz>(() => read(LS.clockTz, ['utc', 'local'] as const, 'utc'));

  // Re-apply the theme/density classes to <html> on mount and on every
  // change. `dark` + `comfort` are the shipped defaults, so a fresh operator
  // gets the same look the app always had.
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('theme-light', theme === 'light');
    root.classList.toggle('theme-dark', theme === 'dark');
    root.classList.toggle('dens-compact', density === 'compact');
    root.classList.toggle('dens-comfort', density === 'comfort');
  }, [theme, density]);

  const setTheme = useCallback((t: Theme) => { setThemeState(t); write(LS.theme, t); }, []);
  const setDensity = useCallback((d: Density) => { setDensityState(d); write(LS.density, d); }, []);
  const setClockTz = useCallback((c: ClockTz) => { setClockTzState(c); write(LS.clockTz, c); }, []);

  const value = useMemo<Preferences>(
    () => ({ theme, density, clockTz, setTheme, setDensity, setClockTz }),
    [theme, density, clockTz, setTheme, setDensity, setClockTz],
  );

  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePreferences(): Preferences {
  const ctx = useContext(PrefsContext);
  if (!ctx) throw new Error('usePreferences must be used within <PreferencesProvider>');
  return ctx;
}
