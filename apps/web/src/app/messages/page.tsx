"use client";
import Link from "next/link";
import { useState } from "react";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { Empty, ErrorState, Loading } from "@/components/States";
import { api, unwrap } from "@/lib/api/client";
import { useLoad } from "@/lib/hooks";

type Msg = { message_id: string; sender: string; text: string };
type Suggestion = { reply: { text: string; proposed_slots: string[]; walk_away: boolean }; intent: { intent: string; summary: string }; compliance?: { violations: unknown[] } };

export default function Messages() {
  const t = useLoad(() => api.GET("/api/messages").then(unwrap), [], 8000);
  const [sel, setSel] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  if (t.loading && !t.data) return <Loading label="Loading client threads…" />;
  if (t.error) return <ErrorState error={t.status === 503 ? "The marketplace is unavailable; threads will load when it is back." : t.error} onRetry={t.reload} />;
  const threads = t.data ?? [];
  const cur = threads.find((x) => x.marketplace_thread_id === sel) ?? threads[0];
  return (
    <div>
      <div className="head">
        <div>
          <h1>Messages</h1>
          <p className="muted small">When a client replies, GigBid drafts a response (and interview slots from your calendar). Nothing is sent until you approve.</p>
        </div>
      </div>
      {note && <div className="banner info" role="status">{note}</div>}
      {threads.length === 0 ? (
        <Empty title="No client conversations yet">Threads appear here when a client replies to one of your proposals.</Empty>
      ) : (
        <div className="grid2" style={{ gridTemplateColumns: "minmax(220px, 1fr) 2fr", alignItems: "start" }}>
          <nav className="panel" style={{ padding: 6 }} aria-label="Threads">
            {threads.map((x) => (
              <button
                key={x.marketplace_thread_id}
                className="ghost"
                style={{ display: "block", width: "100%", textAlign: "left", padding: 10, color: "var(--ink)", background: cur?.marketplace_thread_id === x.marketplace_thread_id ? "var(--info-bg)" : undefined }}
                onClick={() => setSel(x.marketplace_thread_id)}
                aria-current={cur?.marketplace_thread_id === x.marketplace_thread_id}
              >
                <strong>{x.job_title ?? x.job_id}</strong>
                <div className="small muted">{x.state} {x.suggestion ? "· reply ready" : ""}</div>
              </button>
            ))}
          </nav>
          {cur && (
            <div className="stack">
              <section className="panel">
                <h2>{cur.job_title ?? cur.job_id} {cur.job_id && <Link className="small" href={`/jobs/${cur.job_id}`}>job</Link>}</h2>
                {(cur.messages as Msg[]).map((m) => (
                  <div key={m.message_id} style={{ margin: "8px 0", textAlign: m.sender === "client" ? "left" : "right" }}>
                    <span className={`badge ${m.sender === "client" ? "neutral" : "info"}`}>{m.sender}</span>
                    <p className="letter" style={{ display: "inline-block", maxWidth: "85%", textAlign: "left", background: "var(--bg)", padding: "8px 10px", borderRadius: 8, margin: "4px 0" }}>{m.text}</p>
                  </div>
                ))}
              </section>
              {cur.suggestion ? (
                <>
                  <section className="panel">
                    <h2>Suggested reply <span className="badge warn">{(cur.suggestion as Suggestion).intent.intent}</span></h2>
                    <p className="small muted">{(cur.suggestion as Suggestion).intent.summary}</p>
                    {(cur.suggestion as Suggestion).reply.walk_away && <div className="banner bad">Scam signal: the client asked to leave the platform. The reply declines politely.</div>}
                    <p className="letter">{(cur.suggestion as Suggestion).reply.text}</p>
                    {(cur.suggestion as Suggestion).reply.proposed_slots.length > 0 && (
                      <div><h3>Interview slots offered</h3><ul>{(cur.suggestion as Suggestion).reply.proposed_slots.map((s) => <li key={s}>{s}</li>)}</ul></div>
                    )}
                  </section>
                  <ApprovalPanel threadId={cur.graph_thread_id!} kind="message" draftText={(cur.suggestion as Suggestion).reply.text}
                    onDone={(d) => { setNote(`Decision recorded: ${d}`); void t.reload(); }} />
                </>
              ) : (
                <section className="panel row">
                  <span className="muted">{cur.graph_status ? `Assistant status: ${cur.graph_status}` : "No suggestion yet."}</span>
                  <button onClick={async () => { await api.POST("/api/messages/{marketplace_thread_id}/suggest", { params: { path: { marketplace_thread_id: cur.marketplace_thread_id } } }); setNote("Drafting a reply…"); }}>
                    Draft a reply
                  </button>
                </section>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
