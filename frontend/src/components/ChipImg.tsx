import { useState } from "react";

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

interface Props {
  chipId: string;
  size?: number;
  alt?: string;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * Reference-chip thumbnail with a built-in onError fallback.
 *
 * Renders an <img> pointing at /api/reference-chips/{chipId}/image. On 4xx/5xx,
 * swaps to a neutral inline-SVG placeholder + tooltip explaining the chip is
 * unavailable. This avoids the case where an analyst approves a candidate
 * without realising the supporting chip evidence is missing.
 */
export default function ChipImg({ chipId, size = 32, alt, className, style }: Props) {
  const [failed, setFailed] = useState(false);

  const sizeStyle = { width: size, height: size, objectFit: "cover" as const };
  const mergedStyle = { ...sizeStyle, ...(style || {}) };

  if (failed) {
    return (
      <span
        className={className}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--bg-2)",
          border: "1px solid var(--line)",
          color: "var(--ink-3)",
          fontSize: 9,
          opacity: 0.6,
          ...mergedStyle,
        }}
        title="chip image unavailable"
        aria-label="chip image unavailable"
      >
        ✕
      </span>
    );
  }

  return (
    <img
      src={`${API_URL}/api/reference-chips/${chipId}/image`}
      alt={alt ?? `reference chip ${chipId}`}
      loading="lazy"
      className={className}
      style={mergedStyle}
      onError={() => setFailed(true)}
    />
  );
}
