/**
 * AdminAuthTab — LDAP configuration editor + test harness.
 *
 * Reads/writes the singleton ``auth_config`` row via
 *   GET  /api/admin/auth/config
 *   PUT  /api/admin/auth/config
 *   POST /api/admin/auth/test            (full username/password)
 *   POST /api/admin/auth/test-connection (service-bind only, unsaved payload)
 *
 * The env-bootstrap admin (ADMIN_USERNAME) is shown read-only — it can only
 * be changed via .env so it stays trustworthy as a recovery account.
 */

import axios from 'axios';
import {
  CheckCircle2,
  FlaskConical,
  Key,
  Lock,
  RefreshCw,
  Save,
  ShieldAlert,
  XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../hooks/useAuth';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type LDAPConfig = {
  enabled: boolean;
  host: string;
  port: number;
  use_tls: boolean;
  bind_dn: string;
  bind_password: string;
  user_base_dn: string;
  user_search_filter: string;
  attr_username: string;
  attr_displayname: string;
  attr_email: string;
  admin_group_dn: string;
};

const DEFAULT_CONFIG: LDAPConfig = {
  enabled: false,
  host: '',
  port: 389,
  use_tls: false,
  bind_dn: '',
  bind_password: '',
  user_base_dn: '',
  user_search_filter: '(uid={username})',
  attr_username: 'uid',
  attr_displayname: 'cn',
  attr_email: 'mail',
  admin_group_dn: '',
};

export default function AdminAuthTab() {
  const { user } = useAuth();
  const [config, setConfig] = useState<LDAPConfig>(DEFAULT_CONFIG);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testUsername, setTestUsername] = useState('');
  const [testPassword, setTestPassword] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.get<LDAPConfig>(`${API_URL}/api/admin/auth/config`);
      setConfig({ ...DEFAULT_CONFIG, ...data });
      setLoaded(true);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'failed to load config');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const set = useCallback(<K extends keyof LDAPConfig>(key: K, value: LDAPConfig[K]) => {
    setConfig((c) => ({ ...c, [key]: value }));
  }, []);

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    setSavedAt(null);
    try {
      const { data } = await axios.put(`${API_URL}/api/admin/auth/config`, config);
      setConfig({ ...DEFAULT_CONFIG, ...(data?.config || {}) });
      setSavedAt(new Date().toISOString());
      const test = data?.test;
      if (test) {
        setTestResult({
          ok: !!test.ok,
          message: test.ok
            ? `Service bind succeeded · ${test.rtt_ms ?? '?'}ms`
            : test.error || 'service bind failed',
        });
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'save failed');
    } finally {
      setBusy(false);
    }
  }, [config]);

  const testConnection = useCallback(async () => {
    setBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const { data } = await axios.post(`${API_URL}/api/admin/auth/test-connection`, config);
      setTestResult({
        ok: !!data?.ok,
        message: data?.ok
          ? `Service bind succeeded · ${data.rtt_ms ?? '?'}ms`
          : data?.error || 'service bind failed',
      });
    } catch (err: any) {
      setTestResult({
        ok: false,
        message: err?.response?.data?.detail || err?.message || 'test failed',
      });
    } finally {
      setBusy(false);
    }
  }, [config]);

  const testCredentials = useCallback(async () => {
    if (!testUsername || !testPassword) return;
    setBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const { data } = await axios.post(`${API_URL}/api/admin/auth/test`, {
        username: testUsername,
        password: testPassword,
      });
      setTestResult({
        ok: !!data?.ok,
        message: data?.ok
          ? `Authenticated · ${data.user?.username} (${data.user?.role})`
          : data?.error || 'authentication failed',
      });
    } catch (err: any) {
      setTestResult({
        ok: false,
        message: err?.response?.data?.detail || err?.message || 'test failed',
      });
    } finally {
      setBusy(false);
    }
  }, [testUsername, testPassword]);

  const fieldStyle: React.CSSProperties = {
    width: '100%',
    background: 'var(--bg-2)',
    border: '1px solid var(--line)',
    color: 'var(--ink-0)',
    padding: '7px 10px',
    fontSize: 12.5,
    fontFamily: 'var(--font-sans)',
    outline: 'none',
  };

  return (
    <div className="admin-view" style={{ padding: '20px 24px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18, flex: 1, minHeight: 0 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>Authentication</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>
          Bootstrap admin is configured from <b>.env</b>. LDAP is configured here and persisted to PostGIS.
        </div>
      </div>

      {/* Bootstrap admin — read-only */}
      <Card title="Bootstrap admin (env-only)" icon={<Lock size={14} />}>
        <Row label="Username">
          <Static>{user?.role === 'admin' ? user.username : '— (current session is not admin)'}</Static>
        </Row>
        <Row label="Source">
          <Static>ADMIN_USERNAME / ADMIN_PASSWORD environment variables</Static>
        </Row>
        <Row label="Note">
          <Static>
            The env admin always works, even if LDAP is down. Rotate by editing .env and restarting backend.
          </Static>
        </Row>
      </Card>

      {/* LDAP */}
      <Card
        title="LDAP / Active Directory"
        icon={<Key size={14} />}
        right={
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12,
              cursor: 'pointer',
              color: config.enabled ? 'var(--accent)' : 'var(--ink-2)',
            }}
          >
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={(e) => set('enabled', e.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            <span style={{ fontFamily: 'var(--font-mono)', letterSpacing: '.08em' }}>
              {config.enabled ? 'ENABLED' : 'DISABLED'}
            </span>
          </label>
        }
      >
        <Row label="Host">
          <input
            style={fieldStyle}
            placeholder="ldap.example.com"
            value={config.host}
            onChange={(e) => set('host', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="Port">
          <input
            type="number"
            style={fieldStyle}
            value={config.port}
            onChange={(e) => set('port', Number(e.target.value || 0))}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="Use TLS / LDAPS">
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={config.use_tls}
              onChange={(e) => set('use_tls', e.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            <span style={{ color: 'var(--ink-2)' }}>Encrypt the bind (recommended in production).</span>
          </label>
        </Row>
        <Row label="Service bind DN">
          <input
            style={fieldStyle}
            placeholder="cn=svc-sentinel,ou=Services,dc=example,dc=com"
            value={config.bind_dn}
            onChange={(e) => set('bind_dn', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="Service bind password">
          <input
            type="password"
            style={fieldStyle}
            placeholder={config.bind_password === '********' ? '(saved — leave masked to keep)' : ''}
            value={config.bind_password}
            onChange={(e) => set('bind_password', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="User base DN">
          <input
            style={fieldStyle}
            placeholder="ou=People,dc=example,dc=com"
            value={config.user_base_dn}
            onChange={(e) => set('user_base_dn', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="User search filter">
          <input
            style={fieldStyle}
            placeholder="(uid={username})"
            value={config.user_search_filter}
            onChange={(e) => set('user_search_filter', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="Username attribute">
          <input
            style={fieldStyle}
            value={config.attr_username}
            onChange={(e) => set('attr_username', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="Display-name attribute">
          <input
            style={fieldStyle}
            value={config.attr_displayname}
            onChange={(e) => set('attr_displayname', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="Email attribute">
          <input
            style={fieldStyle}
            value={config.attr_email}
            onChange={(e) => set('attr_email', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>
        <Row label="Admin group DN (optional)">
          <input
            style={fieldStyle}
            placeholder="cn=sentinel-admins,ou=Groups,dc=example,dc=com"
            value={config.admin_group_dn}
            onChange={(e) => set('admin_group_dn', e.target.value)}
            disabled={!loaded || busy}
          />
        </Row>

        <div style={{ display: 'flex', gap: 8, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
          <button type="button" className="btn primary" onClick={save} disabled={busy}>
            <Save size={12} /> {busy ? 'Saving…' : 'Save configuration'}
          </button>
          <button type="button" className="btn sm" onClick={testConnection} disabled={busy || !config.host}>
            <FlaskConical size={12} /> Test connection
          </button>
          <button type="button" className="btn sm" onClick={load} disabled={busy}>
            <RefreshCw size={12} /> Reload
          </button>
          {savedAt && (
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--ok)', alignSelf: 'center' }}>
              · saved {new Date(savedAt).toLocaleTimeString()}
            </span>
          )}
        </div>
      </Card>

      {/* Credential test */}
      <Card title="Test a credential" icon={<FlaskConical size={14} />}>
        <Row label="Username">
          <input
            style={fieldStyle}
            placeholder="lastname.firstname"
            value={testUsername}
            onChange={(e) => setTestUsername(e.target.value)}
          />
        </Row>
        <Row label="Password">
          <input
            type="password"
            style={fieldStyle}
            value={testPassword}
            onChange={(e) => setTestPassword(e.target.value)}
          />
        </Row>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button
            type="button"
            className="btn sm primary"
            onClick={testCredentials}
            disabled={busy || !testUsername || !testPassword}
          >
            <FlaskConical size={12} /> Run bind
          </button>
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
            Hits POST /api/admin/auth/test against the saved LDAP config. No session is created.
          </span>
        </div>

        {testResult && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '8px 12px',
              border: `1px solid ${testResult.ok ? 'var(--ok)' : 'var(--nato-hostile)'}`,
              color: testResult.ok ? 'var(--ok)' : 'var(--nato-hostile)',
              background: testResult.ok
                ? 'color-mix(in oklab, var(--ok) 10%, var(--bg-2))'
                : 'color-mix(in oklab, var(--nato-hostile) 10%, var(--bg-2))',
              fontFamily: 'var(--font-mono)',
              fontSize: 11.5,
              marginTop: 8,
            }}
          >
            {testResult.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
            {testResult.message}
          </div>
        )}
      </Card>

      {error && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 12px',
            background: 'color-mix(in oklab, var(--nato-hostile) 12%, var(--bg-2))',
            border: '1px solid var(--nato-hostile)',
            color: 'var(--nato-hostile)',
            fontFamily: 'var(--font-mono)',
            fontSize: 11.5,
          }}
          role="alert"
        >
          <ShieldAlert size={13} /> {error}
        </div>
      )}
    </div>
  );
}

function Card({
  title,
  icon,
  right,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div
      className="card"
      style={{
        background: 'var(--bg-1)',
        border: '1px solid var(--line)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 14px',
          borderBottom: '1px solid var(--line)',
        }}
      >
        {icon}
        <span style={{ fontWeight: 600, fontSize: 13 }}>{title}</span>
        <span style={{ flex: 1 }} />
        {right}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 14 }}>{children}</div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="auth-row">
      <span className="label-mono">{label}</span>
      {children}
    </label>
  );
}

function Static({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="mono"
      style={{
        fontSize: 12,
        color: 'var(--ink-1)',
        background: 'var(--bg-2)',
        border: '1px solid var(--line)',
        padding: '7px 10px',
      }}
    >
      {children}
    </span>
  );
}
