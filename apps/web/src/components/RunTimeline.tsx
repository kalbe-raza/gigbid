"use client";
import { useEffect, useRef, useState } from "react";

export type RunEvent = { id: number; node: string; kind: string; payload: Record<string, unknown>; created_at: string };

/** Live node-by-node progress over Server-Sent Events. Reconnects resume from the last event id, so a refresh or a
 * dropped connection never loses progress. */
export function RunTimeline({ threadId, initial = [], onStatus }: { threadId: string; initial?: RunEvent[]; onStatus?: (s: string) => void }) {
  const [events, setEvents] = useState<RunEvent[]>(initial);
  const [live, setLive] = useState(false);
  const last = useRef<number>(initial.length ? initial[initial.length - 1].id : 0);
  const cb = useRef(onStatus);
  cb.current = onStatus;

  useEffect(() => {
    let es: EventSource | null = null;
    let closed = false;
    const open = () => {
      es = new EventSource(`/api/runs/${encodeURIComponent(threadId)}/stream?after=${last.current}`, { withCredentials: true });
      setLive(true);
      es.addEventListener("node", (m) => {
        const ev = JSON.parse((m as MessageEvent).data) as RunEvent;
        if (ev.id <= last.current) return;
        last.current = ev.id;
        setEvents((xs) => [...xs, ev]);
      });
      es.addEventListener("status", (m) => {
        cb.current?.(JSON.parse((m as MessageEvent).data).status);
        es?.close();
        setLive(false);
      });
      es.onerror = () => {
        es?.close();
        setLive(false);
        if (!closed) setTimeout(open, 3000);
      };
    };
    open();
    return () => {
      closed = true;
      es?.close();
    };
  }, [threadId]);

  return (
    <div>
      <div className="row small muted" style={{ marginBottom: 6 }}>
        {live ? (
          <>
            <span className="spinner" /> streaming live
          </>
        ) : (
          "stream idle"
        )}
      </div>
      {events.length === 0 ? (
        <p className="muted">Waiting for the first node…</p>
      ) : (
        <ol className="timeline" aria-live="polite" data-testid="run-timeline">
          {events.map((e) => {
            const usage = e.payload.usage as { tokens?: number; cost_usd?: number; latency_s?: number } | undefined;
            return (
              <li key={e.id}>
                <span className={`dot ${e.kind}`} aria-hidden />
                <strong>{e.node}</strong>
                <span>
                  {e.kind === "finished" ? String(e.payload.summary ?? "done") : e.kind.replace("_", " ")}
                  {e.kind === "retry" && <span className="muted"> {String(e.payload.error ?? "")}</span>}
                </span>
                <span className="small muted mono">
                  {usage ? `${usage.tokens ?? 0} tok · $${(usage.cost_usd ?? 0).toFixed(5)} · ${usage.latency_s ?? 0}s` : ""}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
