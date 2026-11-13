"use client";
import { ErrorState, Loading } from "@/components/States";
import { api, unwrap } from "@/lib/api/client";
import { pct, useLoad } from "@/lib/hooks";

type Stat = { n: number; mean?: number; p50?: number; p95?: number };

export default function Analytics() {
  const a = useLoad(() => api.GET("/api/analytics").then(unwrap), [], 15000);
  if (a.loading && !a.data) return <Loading />;
  if (a.error) return <ErrorState error={a.error} onRetry={a.reload} />;
  const d = a.data!;
  const cl = d.cost_latency as { per_job_analysed: { cost_usd: Stat; latency_s: Stat }; per_proposal: { cost_usd: Stat; latency_s: Stat } };
  const maxSent = Math.max(1, ...d.reply_rate_by_category.map((r) => Number(r.sent) || 0));
  return (
    <div className="stack">
      <div className="head"><div><h1>Analytics</h1><p className="muted small">Measured from your own pipeline and runs.</p></div></div>
      <div className="grid3">
        <section className="panel"><h3>Funnel</h3>
          {Object.entries(d.funnel).map(([k, v]) => <div key={k} className="row" style={{ justifyContent: "space-between" }}><span style={{ textTransform: "capitalize" }}>{k}</span><strong>{v}</strong></div>)}
        </section>
        <section className="panel"><h3>Connects vs outcomes</h3>
          <p><strong>{String(d.connects.spent ?? 0)}</strong> Connects spent</p>
          <p><strong>{String(d.connects.replies ?? 0)}</strong> replies</p>
          <p className="muted">{d.connects.connects_per_reply ? `${d.connects.connects_per_reply} Connects per reply` : "no replies yet"}</p>
        </section>
        <section className="panel"><h3>Cost &amp; latency</h3>
          <table><thead><tr><th /><th>p50</th><th>p95</th></tr></thead><tbody>
            <tr><td>USD / job analysed</td><td>${(cl.per_job_analysed.cost_usd.p50 ?? 0).toFixed(4)}</td><td>${(cl.per_job_analysed.cost_usd.p95 ?? 0).toFixed(4)}</td></tr>
            <tr><td>s / job analysed</td><td>{cl.per_job_analysed.latency_s.p50 ?? "—"}</td><td>{cl.per_job_analysed.latency_s.p95 ?? "—"}</td></tr>
            <tr><td>USD / proposal</td><td>${(cl.per_proposal.cost_usd.p50 ?? 0).toFixed(4)}</td><td>${(cl.per_proposal.cost_usd.p95 ?? 0).toFixed(4)}</td></tr>
            <tr><td>s / proposal</td><td>{cl.per_proposal.latency_s.p50 ?? "—"}</td><td>{cl.per_proposal.latency_s.p95 ?? "—"}</td></tr>
          </tbody></table>
        </section>
      </div>
      <section className="panel">
        <h2>Reply and hire rate by category</h2>
        {d.reply_rate_by_category.length === 0 ? <p className="muted">No proposals sent yet.</p> : (
          <table><thead><tr><th>Category</th><th>Sent</th><th>Reply rate</th><th>Hire rate</th><th>Connects</th></tr></thead><tbody>
            {d.reply_rate_by_category.map((r) => (
              <tr key={String(r.category)}>
                <td>{String(r.category)}</td>
                <td><div className="meter" style={{ width: 120 }} title={`${r.sent} sent`}><i style={{ width: `${(Number(r.sent) / maxSent) * 100}%` }} /></div><span className="small">{String(r.sent)}</span></td>
                <td>{pct(r.reply_rate as number | null)}</td><td>{pct(r.hire_rate as number | null)}</td><td>{String(r.connects)}</td>
              </tr>
            ))}
          </tbody></table>
        )}
      </section>
      <section className="panel">
        <h2>Proposal experiment (A/B)</h2>
        <p className="muted small">Strategy A is evidence-first; strategy B is short and consultative. Each job gets an arm by a stable hash of its id.</p>
        {(d.experiment ?? []).length === 0 ? <p className="muted">No experiment proposals sent yet.</p> : (
          <table><thead><tr><th>Strategy</th><th>Sent</th><th>Replied</th><th>Reply rate</th></tr></thead><tbody>
            {(d.experiment ?? []).map((r) => (
              <tr key={String(r.arm)}><td>{String(r.arm)}</td><td>{String(r.sent)}</td><td>{String(r.replied)}</td><td>{pct(r.reply_rate as number | null)}</td></tr>
            ))}
          </tbody></table>
        )}
      </section>
      <section className="panel">
        <h2>Top rejection reasons</h2>
        {d.rejection_reasons.length === 0 ? <p className="muted">You have not rejected any drafts.</p> : <ol>{d.rejection_reasons.map((r) => <li key={String(r.reason)}>{String(r.reason)} <span className="badge neutral">{String(r.count)}</span></li>)}</ol>}
      </section>
    </div>
  );
}
