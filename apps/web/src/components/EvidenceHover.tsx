"use client";
import { useState } from "react";

export type EvidenceRef = { doc_id: string; title?: string; snippet?: string };
export type ClaimView = { text: string; status: string; action?: string | null; evidence: EvidenceRef[] };

const CITE = /\[((?:case|repo|review|cv|proposal|pdf):[^\]\s]+)\]/g;
const SENT = /(?<=[.!?])\s+(?=[A-Z"'(\[])/;

function Claim({ sentence, claim, evidence }: { sentence: string; claim?: ClaimView; evidence: EvidenceRef[] }) {
  const [open, setOpen] = useState(false);
  const clean = sentence.replace(CITE, "").replace(/\s+([.,;!?])/g, "$1");
  const status = claim?.status ?? "supported";
  return (
    <span
      className={`claim ${status}`}
      tabIndex={0}
      role="button"
      aria-expanded={open}
      aria-label={`Claim: ${clean}. Status ${status}. ${evidence.length} evidence items. Press Enter to show.`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen(true);
        }
        if (e.key === "Escape") setOpen(false);
      }}
      data-testid="claim"
    >
      {clean}{" "}
      {open && (
        <span className="hovercard" role="tooltip">
          <strong>
            {status === "supported" ? "Supported" : status === "partly_supported" ? "Partly supported" : "Unsupported"}
          </strong>
          {claim?.action && claim.action !== "kept" ? <span className="muted"> · {claim.action} by verifier</span> : null}
          {evidence.length === 0 && <div className="muted">No evidence attached.</div>}
          {evidence.map((e) => (
            <span key={e.doc_id} style={{ display: "block", marginTop: 8 }}>
              <span className="badge info mono">{e.doc_id}</span> <strong>{e.title}</strong>
              <span style={{ display: "block" }} className="muted">
                {(e.snippet || "").slice(0, 280)}
                {(e.snippet || "").length > 280 ? "…" : ""}
              </span>
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

/** Renders a draft where every claim shows the evidence behind it on hover / keyboard focus. */
export function EvidenceHover({ text, claims, evidence }: { text: string; claims: ClaimView[]; evidence: EvidenceRef[] }) {
  const byId = new Map(evidence.map((e) => [e.doc_id, e]));
  const norm = (s: string) => s.replace(CITE, "").replace(/\s+/g, " ").trim().toLowerCase();
  const claimByText = new Map(claims.map((c) => [norm(c.text), c]));
  return (
    <div className="letter" data-testid="evidence-letter">
      {text.split(/\n\s*\n/).map((para, pi) => (
        <p key={pi}>
          {para.split(SENT).map((s, si) => {
            const ids = Array.from(s.matchAll(CITE), (m) => m[1]);
            const claim = claimByText.get(norm(s));
            if (!ids.length && !claim) return <span key={si}>{s} </span>;
            const ev = claim?.evidence?.length ? claim.evidence : ids.map((id) => byId.get(id) ?? { doc_id: id });
            return <Claim key={si} sentence={s} claim={claim} evidence={ev} />;
          })}
        </p>
      ))}
    </div>
  );
}
