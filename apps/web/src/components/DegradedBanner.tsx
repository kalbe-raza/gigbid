"use client";
import { api, unwrap } from "@/lib/api/client";
import { useLoad } from "@/lib/hooks";

/** Polls /api/health. When the marketplace (or another MCP server) is down the user is told, and told that work
 * is queued and will resume - the worker re-queues it with backoff. */
export function DegradedBanner() {
  const h = useLoad(() => api.GET("/api/health").then(unwrap), [], 8000);
  if (h.error && !h.data)
    return (
      <div className="banner bad" role="alert" data-testid="degraded-banner">
        GigBid&apos;s API is not reachable right now. Retrying…
      </div>
    );
  if (!h.data || !h.data.degraded) return null;
  return (
    <div className={`banner ${h.data.marketplace ? "warn" : "bad"}`} role="alert" data-testid="degraded-banner">
      <strong>{h.data.marketplace ? "Degraded" : "Marketplace unavailable"}</strong>
      <span>{h.data.message}</span>
      {h.data.queued_jobs > 0 && <span className="badge neutral">{h.data.queued_jobs} jobs queued</span>}
    </div>
  );
}
