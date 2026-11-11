"use client";
import Link from "next/link";
import { use, useState } from "react";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { EvidenceHover, type ClaimView, type EvidenceRef } from "@/components/EvidenceHover";
import { ErrorState, Loading } from "@/components/States";
import { api, unwrap } from "@/lib/api/client";
import { useLoad } from "@/lib/hooks";

type Draft = { version: number; cover_letter: string; source: string; word_count: number; screening_answers: { question: string; answer: string }[] };
type Critique = { score: number; approve: boolean; issues: { kind: string; detail: string; paragraph?: number | null }[]; instructions: string };
type Verdict = { allowed: boolean; violations: { rule: string; text: string; fix: string }[]; blocked_reason?: string | null; checked_by: string[] };

export default function Studio({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const s = useLoad(() => api.GET("/api/proposals/{job_id}", { params: { path: { job_id: id } } }).then(unwrap), [id], 5000);
  const [regen, setRegen] = useState<{ i: number; text: string } | null>(null);
  const [busyPara, setBusyPara] = useState<number | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (s.loading && !s.data) return <Loading label="Loading the proposal studio…" />;
  if (s.error) return <ErrorState error={s.error} onRetry={s.reload} />;
  const v = s.data!;
  const approval = v.approval as Record<string, unknown> | null;
  const drafts = v.drafts as Draft[];
  const draft = (approval?.draft as Draft | undefined) ?? drafts[drafts.length - 1];
  const claims = (approval?.claims as ClaimView[] | undefined) ?? [];
  const evidence: EvidenceRef[] = claims.flatMap((c) => c.evidence);
  const critique = (v.critiques as Critique[]).at(-1);
  const verdict = (v.compliance as Verdict[]).at(-1);
  const ver = (v.verification as { unsupported_before: number; unsupported_after: number; total_claims: number }[]).at(-1);
  const paragraphs = draft ? draft.cover_letter.split(/\n\s*\n/) : [];

  async function regenerate(i: number) {
    setBusyPara(i);
    try {
      const r = await api.POST("/api/proposals/{job_id}/regenerate", { params: { path: { job_id: id } }, body: { paragraph_index: i } }).then(unwrap);
      setRegen({ i, text: r.text });
    } finally {
      setBusyPara(null);
    }
  }

  return (
    <div className="stack">
      <div className="head">
        <div>
          <Link href={`/jobs/${id}`} className="small">← Job detail</Link>
          <h1>Proposal studio</h1>
          <div className="row small muted">
            <span className={`badge ${v.pending ? "info" : v.status === "submitted" ? "ok" : "neutral"}`}>{v.pending ? "waiting for your approval" : v.status}</span>
            {draft && <span>draft v{draft.version} · {draft.word_count} words · last edited by {draft.source}</span>}
            {v.trace_url && <a href={v.trace_url} target="_blank" rel="noreferrer">trace ↗</a>}
          </div>
        </div>
      </div>
      {done && <div className="banner info" role="status">Decision recorded ({done}). The agent is resuming; this page updates by itself.</div>}
      {v.attention_reason && <div className="banner bad" role="alert">{v.attention_reason}</div>}
      {v.submission && <div className="banner info">Submitted: {JSON.stringify(v.submission)}</div>}

      {!draft ? (
        <div className="panel">No draft yet - this job may have been skipped or is still being analysed.</div>
      ) : (
        <div className="grid2" style={{ alignItems: "start" }}>
          <section className="panel stack">
            <h2>Draft <span className="small muted">hover or focus a sentence to see its evidence</span></h2>
            <EvidenceHover text={draft.cover_letter} claims={claims} evidence={evidence} />
            {draft.screening_answers?.length > 0 && (
              <div>
                <h3>Screening answers</h3>
                {draft.screening_answers.map((a) => (
                  <div key={a.question} style={{ marginBottom: 10 }}>
                    <div className="small muted">{a.question}</div>
                    <EvidenceHover text={a.answer} claims={claims} evidence={evidence} />
                  </div>
                ))}
              </div>
            )}
            <div>
              <h3>Regenerate one paragraph</h3>
              <div className="row">
                {paragraphs.map((_, i) => (
                  <button key={i} disabled={busyPara !== null || !v.pending} onClick={() => regenerate(i)}>
                    {busyPara === i ? "Rewriting…" : `Paragraph ${i + 1}`}
                  </button>
                ))}
              </div>
              {regen && (
                <div className="panel" style={{ marginTop: 10 }}>
                  <div className="small muted">Suggested paragraph {regen.i + 1} (use Edit to apply it; edits are re-checked)</div>
                  <p className="letter">{regen.text}</p>
                </div>
              )}
            </div>
          </section>
          <div className="stack">
            {v.pending && approval && (
              <ApprovalPanel
                threadId={v.thread_id}
                kind="proposal"
                draftText={regen ? paragraphs.map((p, i) => (i === regen.i ? regen.text : p)).join("\n\n") : draft.cover_letter}
                dryRun={approval.dry_run as never}
                onDone={(d) => { setDone(d); void s.reload(); }}
              />
            )}
            <section className="panel">
              <h2>Claim verification</h2>
              {ver ? <p>{ver.total_claims} claims checked against your portfolio; unsupported claims {ver.unsupported_before} → {ver.unsupported_after} after verification.</p> : <p className="muted">—</p>}
            </section>
            <section className="panel" data-testid="critic-notes">
              <h2>Critic {critique && <span className="small muted">score {critique.score}/10 · {critique.approve ? "approved" : "sent back"}</span>}</h2>
              {critique?.issues.length ? <ul>{critique.issues.map((i, k) => <li key={k}><span className="badge warn">{i.kind}</span> {i.detail}</li>)}</ul> : <p className="muted">No open issues.</p>}
              <p className="small muted">{(v.critiques as Critique[]).length} critique round(s); at most 2 revisions.</p>
            </section>
            <section className="panel" data-testid="compliance-notes">
              <h2>Compliance Guard {verdict && <span className={`badge ${verdict.allowed ? "ok" : "bad"}`}>{verdict.allowed ? "allowed" : "blocked"}</span>}</h2>
              {verdict?.blocked_reason && <p className="banner bad">{verdict.blocked_reason}</p>}
              {verdict?.violations.length ? <ul>{verdict.violations.map((x, k) => <li key={k}><span className="badge warn">{x.rule}</span> {x.text.slice(0, 120)} <span className="muted">→ {x.fix}</span></li>)}</ul> : <p className="muted">No policy issues found.</p>}
              {verdict && <p className="small muted">checked by: {verdict.checked_by.join(", ")}</p>}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
