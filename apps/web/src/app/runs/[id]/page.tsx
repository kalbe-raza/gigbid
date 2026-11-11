"use client";
import Link from "next/link";
import { use, useState } from "react";
import { RunTimeline, type RunEvent } from "@/components/RunTimeline";
import { ErrorState, Loading } from "@/components/States";
import { api, unwrap } from "@/lib/api/client";
import { useLoad } from "@/lib/hooks";

export default function RunViewer({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const run = useLoad(() => api.GET("/api/runs/{thread_id}", { params: { path: { thread_id: id } } }).then(unwrap), [id]);
  const hist = useLoad(() => api.GET("/api/runs/{thread_id}/history", { params: { path: { thread_id: id } } }).then(unwrap), [id]);
  const [msg, setMsg] = useState<string | null>(null);
  if (run.loading && !run.data) return <Loading />;
  if (run.error) return <ErrorState error={run.error} onRetry={run.reload} />;
  const r = run.data!;
  const usage = r.usage as { tokens?: number; cost_usd?: number; latency_s?: number; nodes?: Record<string, { tokens: number; cost_usd: number; models: string[] }> };
  return (
    <div className="stack">
      <div className="head">
        <div>
          <Link href="/runs" className="small">← Runs</Link>
          <h1 className="mono" style={{ fontSize: "1.05rem" }}>{r.thread_id}</h1>
          <div className="row small muted">
            <span className="badge neutral">{r.kind}</span>
            <span className={`badge ${r.status === "error" || r.status === "dead_letter" ? "bad" : "info"}`}>{r.status}</span>
            {r.trace_url && <a href={r.trace_url} target="_blank" rel="noreferrer">Full trace in Phoenix ↗</a>}
            {r.ref && r.kind === "bid" && <Link href={`/jobs/${r.ref}`}>job</Link>}
          </div>
        </div>
      </div>
      {r.error && <div className="banner bad">{r.error}</div>}
      {msg && <div className="banner info">{msg}</div>}
      <div className="grid2" style={{ alignItems: "start" }}>
        <section className="panel">
          <h2>Node-by-node progress</h2>
          <RunTimeline threadId={id} initial={r.events as RunEvent[]} onStatus={() => void run.reload()} />
        </section>
        <div className="stack">
          <section className="panel">
            <h2>Cost and tokens</h2>
            <p>{usage.tokens ?? 0} tokens · ${(usage.cost_usd ?? 0).toFixed(5)} {usage.latency_s ? `· ${usage.latency_s}s` : ""}</p>
            {usage.nodes && (
              <table><thead><tr><th>Node</th><th>Tokens</th><th>Cost</th><th>Model</th></tr></thead><tbody>
                {Object.entries(usage.nodes).map(([n, u]) => <tr key={n}><td>{n}</td><td>{u.tokens}</td><td>${u.cost_usd.toFixed(5)}</td><td className="small mono">{u.models.join(", ")}</td></tr>)}
              </tbody></table>
            )}
          </section>
          <section className="panel">
            <h2>Checkpoint history</h2>
            <p className="small muted">Replay re-executes the thread from any checkpoint (the original history is kept).</p>
            {hist.data?.length ? (
              <ol className="small">
                {hist.data.slice(0, 25).map((h) => (
                  <li key={h.checkpoint_id} className="row" style={{ justifyContent: "space-between" }}>
                    <span>step {h.step} · next: {h.next.join(", ") || "—"} · {h.status ?? ""}</span>
                    {h.next.length > 0 && (
                      <button className="ghost" onClick={async () => {
                        await api.POST("/api/runs/{thread_id}/replay/{checkpoint_id}", { params: { path: { thread_id: id, checkpoint_id: h.checkpoint_id } } });
                        setMsg(`Replay from step ${h.step} queued.`);
                      }}>replay</button>
                    )}
                  </li>
                ))}
              </ol>
            ) : <p className="muted">No checkpoints.</p>}
          </section>
        </div>
      </div>
    </div>
  );
}
