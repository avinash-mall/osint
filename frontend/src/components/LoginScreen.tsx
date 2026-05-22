/**
 * LoginScreen — Sentinel · GEOINT Workstation sign-in.
 *
 * Two-pane layout: brand half (graticule + telemetry strip) on the left,
 * credential form on the right. Wired to POST /api/auth/login via useAuth.
 *
 * Changes vs previous revision:
 *   1. LDAP-hint moved ABOVE the Sign in button so first-time admins see it
 *      before they attempt their first failed login. Old position required
 *      a successful sign-in to ever appear visually relevant.
 *   2. Form sets aria-busy while authenticating; status messages use
 *      aria-live for screen-reader users.
 *   3. .login-layout becomes a CSS container (see index.css) and the
 *      two-pane → stacked breakpoint is driven by @container, not @media,
 *      so embedding the login screen in a smaller widget (e.g. a re-auth
 *      modal) collapses to one column based on its own width.
 */

import { useState } from 'react';
import { Building, Key, Lock, Shield, User } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { useDeploymentMode, type DeploymentInfo } from '../hooks/useDeploymentMode';
import { SentinelMark } from './atoms';

export default function LoginScreen() {
  const { login, error } = useAuth();
  const deployment = useDeploymentMode();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const canSubmit = !busy && !!username.trim() && !!password;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch {
      // surfaces via useAuth().error
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="login-screen"
      style={{
        height: '100%', width: '100%',
        display: 'flex', flexDirection: 'column',
        background: 'var(--bg-0)', color: 'var(--ink-0)',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <DeploymentBar info={deployment} />

      <div
        className="login-layout"
        style={{ flex: 1, containerType: 'inline-size', containerName: 'login' }}
      >
        {/* ===== Brand pane ===== */}
        <div
          className="login-brand-pane"
          style={{
            position: 'relative',
            overflow: 'hidden',
            background: 'linear-gradient(135deg, var(--bg-0) 0%, var(--bg-1) 100%)',
            borderRight: '1px solid var(--line)',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <GraticuleBG />

          <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 14 }}>
            <SentinelMark size={44} title="Sentinel" />
            <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
              <span style={{ fontSize: 18, fontWeight: 600 }}>Sentinel</span>
              <span className="mono" style={{ color: 'var(--ink-2)', fontSize: 11, letterSpacing: '.08em' }}>
                GEOINT WORKSTATION
              </span>
            </div>
          </div>

          <div
            style={{
              position: 'relative',
              marginTop: 'auto',
              display: 'flex', flexDirection: 'column',
              gap: 18,
            }}
          >
            <h1
              style={{
                margin: 0, fontSize: 'var(--text-hero)', lineHeight: 1.05,
                fontWeight: 500, letterSpacing: '-0.01em',
                maxInlineSize: '35rem',
              }}
            >
              All-source geospatial intelligence.
              <br />
              <span style={{ color: 'var(--accent)' }}>One workstation.</span>
            </h1>
            <p
              style={{
                margin: 0, color: 'var(--ink-1)',
                fontSize: 'var(--text-md)', lineHeight: 1.55,
                maxInlineSize: '32.5rem',
              }}
            >
              SAM 3, DINOv3, Prithvi and TerraMind fused on a single map. Sign in to your operator
              profile to resume your last AOI.
            </p>

            <TelemetryStrip />
          </div>
        </div>

        {/* ===== Auth pane ===== */}
        <form
          className="login-auth-pane"
          onSubmit={onSubmit}
          aria-busy={busy}
          style={{
            background: 'var(--bg-1)',
            display: 'flex', flexDirection: 'column',
            justifyContent: 'center', gap: 24,
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span className="label-mono">Operator sign-in</span>
            <h2 style={{ margin: 0, fontSize: 26, fontWeight: 500 }}>Resume operations</h2>
            <span style={{ color: 'var(--ink-2)', fontSize: 13 }}>
              Authenticate with the env-bootstrap admin or your enterprise LDAP account.
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Field label="Username or email">
              <Input
                icon={<User size={14} style={{ color: 'var(--ink-2)' }} aria-hidden/>}
                value={username}
                onChange={setUsername}
                placeholder="lastname.firstname"
                autoComplete="username" autoFocus
                disabled={busy}
                name="username"
              />
            </Field>

            <Field label="Password">
              <Input
                icon={<Lock size={14} style={{ color: 'var(--ink-2)' }} aria-hidden/>}
                value={password}
                onChange={setPassword}
                placeholder="••••••••••••"
                type="password"
                autoComplete="current-password"
                disabled={busy}
                name="password"
              />
            </Field>

            {/* LDAP hint — visible BEFORE first sign-in attempt now */}
            <div
              className="login-ldap-hint"
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                color: 'var(--ink-2)',
                fontFamily: 'var(--font-mono)',
                fontSize: 10.5, letterSpacing: '.06em',
                padding: '8px 12px',
                background: 'var(--bg-2)',
                border: '1px solid var(--line)',
                borderRadius: 6,
              }}
            >
              <Building size={11} aria-hidden/>
              <span>
                LDAP enabled? Configure it under <b style={{ color: 'var(--ink-1)' }}>Admin · Auth</b> after
                signing in as the env admin.
              </span>
            </div>

            {error && (
              <div
                role="alert"
                aria-live="polite"
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '8px 12px',
                  background: 'color-mix(in oklab, var(--nato-hostile) 12%, var(--bg-2))',
                  border: '1px solid var(--nato-hostile)',
                  color: 'var(--nato-hostile)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11.5, letterSpacing: '.02em',
                }}
              >
                <Shield size={13} aria-hidden/>
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={busy || !username.trim() || !password}
              className="btn primary"
              aria-disabled={busy || !username.trim() || !password}
              style={{
                height: 42, fontSize: 13,
                opacity: busy || !username.trim() || !password ? 0.6 : 1,
                cursor: busy ? 'wait' : 'pointer',
              }}
            >
              <Key size={14} aria-hidden/>
              <span aria-live="polite">{busy ? 'Authenticating…' : 'Sign in'}</span>
              <span style={{ flex: 1 }} />
              <span className="kbd" style={{ marginLeft: 0, opacity: canSubmit ? 1 : 0.35 }}>↵</span>
            </button>

            {/* First-time / recovery path — there is otherwise no way to
                recover access without SSH-ing to the host (UX-AUDIT F4). */}
            <details className="login-reset">
              <summary className="login-reset-link">Can’t sign in? Reset via env bootstrap</summary>
              <p style={{
                margin: '8px 0 0', fontSize: 11, lineHeight: 1.5, color: 'var(--ink-2)',
              }}>
                The first administrator is bootstrapped from <span className="mono">ADMIN_USERNAME</span> /{' '}
                <span className="mono">ADMIN_PASSWORD</span> in the deployment <span className="mono">.env</span>.
                Update those values and restart the backend to recover access.
              </p>
              {deployment.supportContact && (
                <p className="login-support-contact" style={{ margin: '6px 0 0' }}>
                  LDAP support · {deployment.supportContact}
                </p>
              )}
            </details>
          </div>

          <div
            style={{
              display: 'flex', alignItems: 'center',
              justifyContent: 'space-between',
              paddingTop: 18,
              borderTop: '1px solid var(--line)',
              color: 'var(--ink-2)', fontSize: 11,
            }}
          >
            <span className="mono">BUILD · sentinel/main</span>
            <span className="mono">AUTH · local</span>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * Deployment banner (UX-AUDIT F1). Replaces the hardcoded
 * `UNCLASSIFIED // FOR OFFICIAL USE ONLY` bar, which a stock open-source
 * clone cannot back. The posture comes from `/api/system/deployment-mode`
 * and defaults to `demo`; operators opt in to gov/mil framing by setting
 * `SENTINEL_DEPLOYMENT_MODE` (see README).
 */
function DeploymentBar({ info }: { info: DeploymentInfo }) {
  if (info.mode === 'internal') {
    return (
      <div role="banner" className="deploy-bar deploy-bar--internal">
        {info.label || 'INTERNAL DEPLOYMENT'}
      </div>
    );
  }
  if (info.mode === 'accredited') {
    return (
      <div role="banner" className="deploy-bar deploy-bar--accredited">
        {info.label || 'ACCREDITED DEPLOYMENT'}
      </div>
    );
  }
  return (
    <div role="banner" className="deploy-bar deploy-bar--demo">
      {info.label || 'DEMO BUILD · NOT FOR OPERATIONAL USE'}
    </div>
  );
}

function GraticuleBG() {
  return (
    <svg width="100%" height="100%" preserveAspectRatio="none"
      style={{ position: 'absolute', inset: 0, opacity: 0.55, pointerEvents: 'none' }}
      aria-hidden
    >
      <defs>
        <pattern id="login-grid-lg" width="80" height="80" patternUnits="userSpaceOnUse">
          <path d="M80 0 H0 V80" fill="none" stroke="var(--line-2)" strokeWidth=".6" />
        </pattern>
        <pattern id="login-grid-sm" width="16" height="16" patternUnits="userSpaceOnUse">
          <path d="M16 0 H0 V16" fill="none" stroke="var(--line)" strokeWidth=".4" />
        </pattern>
        <radialGradient id="login-vign" cx="50%" cy="40%" r="70%">
          <stop offset="0%" stopColor="color-mix(in oklab, var(--accent) 12%, transparent)" />
          <stop offset="60%" stopColor="transparent" />
        </radialGradient>
      </defs>
      <rect width="100%" height="100%" fill="url(#login-grid-sm)" />
      <rect width="100%" height="100%" fill="url(#login-grid-lg)" />
      <rect width="100%" height="100%" fill="url(#login-vign)" />
      <g fill="none" stroke="color-mix(in oklab, var(--accent) 55%, transparent)" strokeWidth=".8">
        <path d="M -20 480 Q 360 60 820 540" />
        <path d="M  80 760 Q 600 200 1100 700" opacity=".5" />
        <path d="M -40 220 Q 380 520 760 220" opacity=".35" />
      </g>
      <g>
        {[
          { x: 220, y: 280, r: 4 },
          { x: 540, y: 380, r: 3 },
          { x: 380, y: 600, r: 3.2 },
          { x: 720, y: 220, r: 2.8 },
        ].map((p, i) => (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r={p.r * 4}
              fill="color-mix(in oklab, var(--accent) 40%, transparent)" opacity=".25" />
            <circle cx={p.x} cy={p.y} r={p.r} fill="var(--accent)" />
          </g>
        ))}
      </g>
    </svg>
  );
}

function TelemetryStrip() {
  return (
    <div
      className="telemetry-strip"
      style={{
        marginTop: 18,
        border: '1px solid var(--line)',
        background: 'color-mix(in oklab, var(--bg-1) 92%, transparent)',
      }}
    >
      {[
        { l: 'SYSTEM', v: 'NOMINAL', tone: 'var(--ok)' },
        { l: 'AUTH', v: 'LOCAL · READY' },
        { l: 'POSTGIS', v: 'CONNECTED' },
        { l: 'NEO4J', v: 'CONNECTED' },
      ].map((s, i, arr) => (
        <div
          key={i}
          style={{
            padding: '14px 16px',
            borderRight: i < arr.length - 1 ? '1px solid var(--line)' : '0',
            display: 'flex', flexDirection: 'column', gap: 4,
          }}
        >
          <span className="label-mono">{s.l}</span>
          <span className="mono" style={{ color: (s as any).tone || 'var(--ink-0)', fontSize: 12 }}>
            {s.v}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * Field label. UX-AUDIT F3: dropped the all-caps mono treatment — that
 * style is now reserved for true metadata (build hash, AOR, lat/lon), not
 * form labels the operator reads as plain words.
 */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--ink-1)' }}>{label}</span>
      {children}
    </label>
  );
}

type InputProps = {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  icon?: React.ReactNode;
  type?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  autoComplete?: string;
  name?: string;
};

function Input({ value, onChange, placeholder, icon, type, disabled, autoFocus, autoComplete, name }: InputProps) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      height: 38, padding: '0 10px',
      background: 'var(--bg-0)', border: '1px solid var(--line-2)',
    }}>
      {icon}
      <input
        type={type || 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        autoComplete={autoComplete}
        name={name}
        style={{
          flex: 1, border: 0, outline: 'none',
          background: 'transparent', color: 'var(--ink-0)',
          fontFamily: 'inherit', fontSize: 13,
        }}
      />
    </div>
  );
}
