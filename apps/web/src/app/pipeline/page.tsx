"use client";
import Link from "next/link";
import { Empty, ErrorState, Loading } from "@/components/States";
import { api, unwrap } from "@/lib/api/client";
import { ago, fmtMoney, useLoad } from "@/lib/hooks";

const STAGES = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"] as const;

export default function Pipeline() {
  const p = useLoad(() => api.GET("/api/pipeline").then(unwrap), [], 7000);
  if (p.loading && !p.data) return <Loading />;
  if (p.error) return <ErrorState error={p.error} onRetry={p.reload} />;
  const cards = p.data ?? [];
  return (
    <div>
      <div className="head">
        <div>
          <h1>Pipeline</h1>
          <p className="muted small">Cards move automatically as marketplace events arrive (viewed, replied, interview, offer).</p>
        </div>
      </div>
      {cards.length === 0 ? (
        <Empty title="Nothing in the pipeline yet">Approve a proposal in the studio and it appears here.</Empty>
      ) : (
        <div className="kanban" data-testid="kanban">
          {STAGES.map((s) => {
            const col = cards.filter((c) => c.stage === s);
            return (
              <section className="kcol" key={s} aria-label={`${s} (${col.length})`}>
                <h3 className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ textTransform: "capitalize" }}>{s}</span>
                  <span className="badge neutral">{col.length}</span>
                </h3>
                {col.map((c) => (
                  <div className="kcard" key={c.job_id}>
                    <Link href={`/jobs/${c.job_id}`}>{c.title}</Link>
                    <div className="small muted">
                      {c.category} · {fmtMoney(c.amount)} · {c.connects} Connects · {ago(c.updated_at)}
                    </div>
                  </div>
                ))}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
