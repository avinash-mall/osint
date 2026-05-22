/**
 * useDeploymentMode — resolves the login banner's deployment posture.
 *
 * UX-AUDIT F1: the login screen used to hardcode a `UNCLASSIFIED // FOR
 * OFFICIAL USE ONLY` banner that a stock open-source clone cannot back.
 * The mode now comes from `GET /api/system/deployment-mode`, which reads
 * `SENTINEL_DEPLOYMENT_MODE` on the backend and defaults to `demo`. Any
 * fetch failure also falls back to `demo` — the conservative posture.
 */

import { useEffect, useState } from 'react';
import axios from 'axios';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type DeploymentMode = 'demo' | 'internal' | 'accredited';

export type DeploymentInfo = {
  mode: DeploymentMode;
  /** Banner text. Backend-provided for internal/accredited; null while loading. */
  label: string | null;
  /** Optional admin contact for LDAP deployments (SENTINEL_AUTH_SUPPORT_CONTACT). */
  supportContact: string | null;
};

const VALID: DeploymentMode[] = ['demo', 'internal', 'accredited'];

export function useDeploymentMode(): DeploymentInfo {
  const [info, setInfo] = useState<DeploymentInfo>({ mode: 'demo', label: null, supportContact: null });

  useEffect(() => {
    let cancelled = false;
    axios
      .get(`${API_URL}/api/system/deployment-mode`)
      .then((r) => {
        if (cancelled) return;
        const mode = VALID.includes(r.data?.mode) ? (r.data.mode as DeploymentMode) : 'demo';
        setInfo({
          mode,
          label: typeof r.data?.label === 'string' ? r.data.label : null,
          supportContact: typeof r.data?.support_contact === 'string' ? r.data.support_contact : null,
        });
      })
      .catch(() => {
        if (!cancelled) setInfo({ mode: 'demo', label: null, supportContact: null });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return info;
}
