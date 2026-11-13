"use client";
import Link from "next/link";
import { use, useState } from "react";
import { ActionBadge, ErrorState, Loading, Meter, RiskBadge } from "@/components/States";
import { RunTimeline } from "@/components/RunTimeline";
import { api, unwrap } from "@/lib/api/client";
import { fmtMoney, useLoad } from "@/lib/hooks";

type Req = { text: string; skill?: string | null; priority: string };
type Match = { requirement: string; status: string; evidence: { doc_id: string; title: string; snippet: string }[] };
type Flag = { code: string; severity: string; quote: string };

export default function JobDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [queued, setQueued] = useState(false);
  const d = useLoad(() => api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } }).then(unwrap), [id], 8000);
  if (d.loading && !d.data) return <Loading />;
  if (d.error) return <ErrorState error={d.error} onRetry={d.reload} />;
  const v = d.data!;
  const job = v.job as Record<string, unknown>;
  const spec = v.job_spec as { summary?: string; deliverables?: string[]; requirements?: Req[]; screening_questions?: string[]; hidden_needs?: string[]; timeline?: string } | null;
  const risk = v.risk as { score: number; level: string; is_scam: boolean; injection_detected: boolean; flags: Flag[]; explanation: string; client: { score: number; notes: string[]; payment_verified?: boolean; total_spent?: number; rating?: number } } | null;
  const fit = v.fit as { score: number; matrix: Match[]; reasons: string[]; retrieval_variant?: string } | null;
  const decision = v.decision as { action: string; reasons: string[]; confidence: number } | null;
  const price = v.price as { amount: number; bid_type: string; rationale: string[]; connects_cost: number; connects_worth_it: boolean } | null;

  return (
    <div className="stack">
      <div className="head">
        <div>
          <Link href="/inbox" className="small">← Inbox</Link>
          <h1>{String(job.title ?? id)}</h1>
          <div className="row small muted">
            <span>{String(job.category ?? "")}</span>
            <ActionBadge action={decision?.action} status={v.status} />
            {risk && <RiskBadge level={risk.level} scam={risk.is_scam} injection={risk.injection_detected} />}
            {v.trace_url && <a href={v.trace_url} target="_blank" rel="noreferrer">full trace ↗</a>}
            {v.thread_id && <Link href={`/runs/${v.thread_id}`}>agent run</Link>}
          </div>
        </div>
        {v.status === "awaiting_approval" && <Link className="btn primary" href={`/studio/${id}`}>Open proposal studio</Link>}
        {!["awaiting_approval", "running", "resuming", "submitted"].includes(String(v.status)) && (
          <button className="btn" disabled={queued} onClick={async () => {
            setQueued(true);
            await api.POST("/api/jobs/{job_id}/analyse", { params: { path: { job_id: id } } });
            d.reload();
          }}>{queued ? "Queued for analysis" : v.thread_id ? "Analyse again" : "Analyse now"}</button>
        )}
      </div>

      {!v.thread_id && <div className="banner info">This job is queued for analysis.</div>}
      {(v.errors ?? []).length > 0 && <div className="banner warn">{(v.errors ?? []).join(" · ")}</div>}

      {decision && (
        <section className="panel">
          <h2>Bid or skip: {decision.action.toUpperCase()}</h2>
          <ul>{decision.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </section>
      )}

      <div className="grid2">
        <section className="panel">
          <h2>JobSpec</h2>
          {spec ? (
            <>
              <p>{spec.summary}</p>
              <h3>Deliverables</h3>
              <ul>{(spec.deliverables ?? []).map((x) => <li key={x}>{x}</li>)}</ul>
              <h3>Requirements</h3>
              <ul>{(spec.requirements ?? []).map((r) => <li key={r.text}>{r.text} <span className={`badge ${r.priority === "must" ? "info" : "neutral"}`}>{r.priority}</span></li>)}</ul>
              {!!spec.screening_questions?.length && (<><h3>Screening questions</h3><ul>{spec.screening_questions.map((q) => <li key={q}>{q}</li>)}</ul></>)}
              {!!spec.hidden_needs?.length && (<><h3>Hidden needs</h3><ul>{spec.hidden_needs.map((q) => <li key={q}>{q}</li>)}</ul></>)}
              <p className="small muted">Budget: {job.is_hourly ? `${fmtMoney(job.hourly_low as number)}–${fmtMoney(job.hourly_high as number, true)}` : fmtMoney(job.budget as number)} {spec.timeline ? `· timeline ${spec.timeline}` : ""}</p>
            </>
          ) : <p className="muted">Not analysed yet.</p>}
        </section>
        <section className="panel">
          <h2>Risk report {risk && <span className="muted small">score {risk.score.toFixed(2)}</span>}</h2>
          {risk ? (
            <>
              <p>{risk.explanation}</p>
              {risk.flags.length === 0 ? <p className="muted">No flags.</p> : (
                <ul>{risk.flags.map((f, i) => <li key={i}><span className={`badge ${f.severity === "high" ? "bad" : f.severity === "medium" ? "warn" : "neutral"}`}>{f.code}</span> {f.quote && <q>{f.quote}</q>}</li>)}</ul>
              )}
              <h3>Client</h3>
              <p className="small">Quality {risk.client.score.toFixed(2)} · payment {risk.client.payment_verified ? "verified" : "unverified"} · spent {fmtMoney(risk.client.total_spent)} · rating {risk.client.rating ?? "—"}</p>
              {risk.client.notes.map((n) => <div key={n} className="small muted">• {n}</div>)}
            </>
          ) : <p className="muted">Pending.</p>}
        </section>
      </div>

      <section className="panel">
        <h2>Requirement → evidence {fit && <span className="small muted">fit {fit.score.toFixed(2)} · retrieval {fit.retrieval_variant}</span>}</h2>
        {fit ? (
          <div className="table-wrap">
            <table className="matrix" data-testid="evidence-matrix">
              <thead><tr><th>Requirement</th><th>Status</th><th>Portfolio evidence that proves it</th></tr></thead>
              <tbody>
                {fit.matrix.map((m) => (
                  <tr key={m.requirement}>
                    <td>{m.requirement}</td>
                    <td><span className={`badge ${m.status === "proven" ? "ok" : m.status === "partial" ? "warn" : "bad"}`}>{m.status}</span></td>
                    <td>{m.evidence.length === 0 ? <span className="muted">none</span> : m.evidence.slice(0, 3).map((e) => (
                      <div key={e.doc_id} className="small"><span className="badge info mono">{e.doc_id}</span> {e.title} <span className="muted">— {e.snippet.slice(0, 120)}…</span></div>
                    ))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="muted">Pending.</p>}
        {fit && <div className="row" style={{ marginTop: 8 }}><span className="small muted">Overall fit</span><Meter value={fit.score} /></div>}
      </section>

      {price && (
        <section className="panel">
          <h2>Price: {fmtMoney(price.amount, price.bid_type === "hourly")} ({price.bid_type}) · {price.connects_cost} Connects {price.connects_worth_it ? <span className="badge ok">worth it</span> : <span className="badge warn">marginal</span>}</h2>
          <ul>{price.rationale.map((r) => <li key={r}>{r}</li>)}</ul>
        </section>
      )}

      {v.thread_id && (
        <section className="panel">
          <h2>Live agent progress</h2>
          <RunTimeline threadId={v.thread_id} onStatus={() => void d.reload()} />
        </section>
      )}
      <section className="panel">
        <h2>Original post <span className="small muted">(untrusted text)</span></h2>
        <p className="letter small">{String(job.description ?? "")}</p>
      </section>
    </div>
  );
}
