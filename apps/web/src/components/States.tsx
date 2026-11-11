"use client";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" /> {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="banner bad" role="alert">
      <span>Something went wrong: {error}</span>
      {onRetry && (
        <button className="ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="state panel">
      <h3>{title}</h3>
      <div className="small">{children}</div>
    </div>
  );
}

export function RiskBadge({ level, scam, injection }: { level?: string | null; scam?: boolean; injection?: boolean }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      {scam && <span className="badge bad">scam</span>}
      {injection && <span className="badge warn" title="Post contains instructions aimed at AI tools; ignored">injection</span>}
      {!scam && level && (
        <span className={`badge ${level === "high" ? "bad" : level === "medium" ? "warn" : "ok"}`}>risk {level}</span>
      )}
    </span>
  );
}

export function ActionBadge({ action, status }: { action?: string | null; status: string }) {
  if (status === "awaiting_approval") return <span className="badge info">needs your approval</span>;
  if (status === "analysing" || status === "queued") return <span className="badge neutral">{status}…</span>;
  if (status === "submitted") return <span className="badge ok">submitted</span>;
  if (status === "blocked" || status === "needs_attention" || status === "dead_letter")
    return <span className="badge bad">{status.replace("_", " ")}</span>;
  const cls = action === "bid" ? "ok" : action === "watch" ? "warn" : "neutral";
  return <span className={`badge ${cls}`}>{action ?? status}</span>;
}

export function Meter({ value }: { value?: number | null }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
      <div className="meter" aria-hidden>
        <i style={{ width: `${v * 100}%` }} />
      </div>
      <span className="small">{value === null || value === undefined ? "—" : v.toFixed(2)}</span>
    </div>
  );
}
