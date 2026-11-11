"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api/client";
import { fmtMoney } from "@/lib/hooks";

type DryRun = {
  connects_cost?: number | null;
  connects_balance_after?: number | null;
  connects_surcharges?: string[];
  policy_warnings?: string[];
  would_send?: { cover_letter?: string; bid_amount?: number; bid_type?: string; screening_answers?: string[] } | null;
};

/** Approve / edit / reject. Approving sends exactly the dry-run preview shown here (the API signs that text). */
export function ApprovalPanel({
  threadId,
  kind,
  draftText,
  dryRun,
  onDone,
}: {
  threadId: string;
  kind: "proposal" | "message";
  draftText: string;
  dryRun?: DryRun | null;
  onDone: (decision: string) => void;
}) {
  const [mode, setMode] = useState<"idle" | "edit" | "reject">("idle");
  const [text, setText] = useState(draftText);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function send(decision: "approve" | "edit" | "reject") {
    setBusy(true);
    setErr(null);
    try {
      await api
        .POST("/api/approvals/{thread_id}", {
          params: { path: { thread_id: threadId } },
          body: { decision, edited_text: decision === "edit" ? text : null, reason: decision === "reject" ? reason : null },
        })
        .then(unwrap);
      onDone(decision);
      setMode("idle");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const ws = dryRun?.would_send;
  return (
    <section className="panel stack" aria-label="Approval" data-testid="approval-panel">
      <h2>Your decision</h2>
      {kind === "proposal" && dryRun && (
        <div className="stack">
          <div className="row">
            <span className="badge info">dry run</span>
            <span>
              Sends a {ws?.bid_type ?? ""} bid of <strong>{fmtMoney(ws?.bid_amount, ws?.bid_type === "hourly")}</strong> and spends{" "}
              <strong>{dryRun.connects_cost ?? "?"} Connects</strong>
              {dryRun.connects_surcharges?.length ? ` (incl. ${dryRun.connects_surcharges.join(", ")})` : ""}; balance after:{" "}
              {dryRun.connects_balance_after ?? "?"}
            </span>
          </div>
          {dryRun.policy_warnings?.map((w) => (
            <div key={w} className="banner warn">
              {w}
            </div>
          ))}
          <details>
            <summary>Exactly what the client will receive</summary>
            <div className="letter panel" style={{ marginTop: 8 }} data-testid="dry-run-text">
              {ws?.cover_letter}
            </div>
          </details>
        </div>
      )}
      {mode === "edit" && (
        <div className="field">
          <label htmlFor="edit-text">Edit the draft (your edit is re-checked by the Compliance Guard; repeated edits teach style memory)</label>
          <textarea id="edit-text" value={text} onChange={(e) => setText(e.target.value)} data-testid="edit-text" />
        </div>
      )}
      {mode === "reject" && (
        <div className="field">
          <label htmlFor="reject-reason">Why are you rejecting it? (feeds analytics)</label>
          <input
            id="reject-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. budget too low, not my kind of client"
            data-testid="reject-reason"
          />
        </div>
      )}
      {err && (
        <div className="banner bad" role="alert">
          {err}
        </div>
      )}
      <div className="row">
        {mode === "idle" && (
          <>
            <button className="primary" disabled={busy} onClick={() => send("approve")} data-testid="approve">
              {kind === "proposal" ? `Approve & send${dryRun?.connects_cost ? ` (${dryRun.connects_cost} Connects)` : ""}` : "Approve & send"}
            </button>
            <button disabled={busy} onClick={() => setMode("edit")} data-testid="edit">
              Edit
            </button>
            <button className="danger" disabled={busy} onClick={() => setMode("reject")} data-testid="reject">
              Reject
            </button>
          </>
        )}
        {mode === "edit" && (
          <>
            <button className="primary" disabled={busy || !text.trim()} onClick={() => send("edit")} data-testid="save-edit">
              Save edit &amp; re-check
            </button>
            <button onClick={() => setMode("idle")}>Cancel</button>
          </>
        )}
        {mode === "reject" && (
          <>
            <button className="danger" disabled={busy || !reason.trim()} onClick={() => send("reject")} data-testid="confirm-reject">
              Reject with reason
            </button>
            <button onClick={() => setMode("idle")}>Cancel</button>
          </>
        )}
        {busy && <span className="spinner" aria-label="sending" />}
      </div>
      <p className="small muted">Nothing is sent to the marketplace until you approve. Auto-send does not exist.</p>
    </section>
  );
}
