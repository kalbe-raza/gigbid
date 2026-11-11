"use client";
import Link from "next/link";
import { Empty, ErrorState, Loading } from "@/components/States";
import { api, unwrap } from "@/lib/api/client";
import { ago, useLoad } from "@/lib/hooks";

export default function Runs() {
  const r = useLoad(() => api.GET("/api/runs").then(unwrap), [], 6000);
  if (r.loading && !r.data) return <Loading />;
  if (r.error) return <ErrorState error={r.error} onRetry={r.reload} />;
  const runs = r.data ?? [];
  return (
    <div>
      <div className="head"><div><h1>Agent runs</h1><p className="muted small">Every graph thread, with live progress and a link to its full trace.</p></div></div>
      {runs.length === 0 ? <Empty title="No runs yet" /> : (
        <div className="panel table-wrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Thread</th><th>Kind</th><th>Status</th><th>Tokens</th><th>Cost</th><th>Trace</th></tr></thead>
            <tbody>
              {runs.map((x) => {
                const u = x.usage as { tokens?: number; cost_usd?: number };
                return (
                  <tr key={x.thread_id}>
                    <td><Link href={`/runs/${x.thread_id}`} className="mono">{x.thread_id.slice(0, 40)}</Link><div className="small muted">{ago(x.created_at)}</div></td>
                    <td>{x.kind}</td>
                    <td><span className={`badge ${x.status === "error" || x.status === "dead_letter" ? "bad" : x.status === "awaiting_approval" ? "info" : "neutral"}`}>{x.status}</span></td>
                    <td>{u.tokens ?? "—"}</td>
                    <td>{u.cost_usd !== undefined ? `$${u.cost_usd.toFixed(5)}` : "—"}</td>
                    <td>{x.trace_url ? <a href={x.trace_url} target="_blank" rel="noreferrer">open ↗</a> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
