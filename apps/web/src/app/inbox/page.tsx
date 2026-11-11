"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ActionBadge, Empty, ErrorState, Loading, Meter, RiskBadge } from "@/components/States";
import { api, unwrap } from "@/lib/api/client";
import { ago, fmtMoney, useLoad } from "@/lib/hooks";

export default function Inbox() {
  const router = useRouter();
  const [filter, setFilter] = useState<"all" | "approval" | "bid" | "skipped">("all");
  const [hideSkipped, setHideSkipped] = useState(true);
  const jobs = useLoad(() => api.GET("/api/jobs").then(unwrap), [], 6000);
  const [scanning, setScanning] = useState(false);

  const rows = useMemo(() => {
    const xs = jobs.data ?? [];
    return xs.filter((j) => {
      if (hideSkipped && filter !== "skipped" && (j.status === "skipped" || j.status === "dismissed")) return false;
      if (filter === "approval") return j.status === "awaiting_approval";
      if (filter === "bid") return j.action === "bid";
      if (filter === "skipped") return j.status === "skipped" || j.status === "dismissed" || j.action === "skip";
      return true;
    });
  }, [jobs.data, filter, hideSkipped]);

  async function skip(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    await api.POST("/api/jobs/{job_id}/skip", { params: { path: { job_id: id } } });
    void jobs.reload();
  }

  return (
    <div>
      <div className="head">
        <div>
          <h1>Job inbox</h1>
          <p className="muted small">New jobs from your saved searches, ranked by fit. Risky posts are flagged before you spend Connects.</p>
        </div>
        <div className="row">
          <select aria-label="Filter jobs" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} style={{ width: 200 }}>
            <option value="all">All jobs</option>
            <option value="approval">Needs my approval</option>
            <option value="bid">Recommended bids</option>
            <option value="skipped">Skipped</option>
          </select>
          <label className="row small" style={{ gap: 4, margin: 0 }}>
            <input type="checkbox" style={{ width: "auto" }} checked={hideSkipped} onChange={(e) => setHideSkipped(e.target.checked)} /> hide skipped
          </label>
          <button
            disabled={scanning}
            onClick={async () => {
              setScanning(true);
              await api.POST("/api/jobs/scan");
              setTimeout(() => { setScanning(false); void jobs.reload(); }, 3000);
            }}
          >
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>
      {jobs.loading && !jobs.data && <Loading label="Loading your inbox…" />}
      {jobs.error && <ErrorState error={jobs.error} onRetry={jobs.reload} />}
      {jobs.data && rows.length === 0 && (
        <Empty title="No jobs here yet">The scout runs your saved searches every 15 minutes. Use “Scan now” to run it immediately.</Empty>
      )}
      {rows.length > 0 && (
        <div className="panel table-wrap" style={{ padding: 0 }}>
          <table data-testid="inbox-table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Fit</th>
                <th>Risk</th>
                <th>Recommendation</th>
                <th>Price</th>
                <th>Connects</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((j) => (
                <tr key={j.job_id} className="clickable" onClick={() => router.push(`/jobs/${j.job_id}`)} data-testid="inbox-row">
                  <td>
                    <Link href={`/jobs/${j.job_id}`} onClick={(e) => e.stopPropagation()}>{j.title}</Link>
                    <div className="small muted">{j.category} · {ago(j.updated_at)}{j.duplicate_of ? ` · duplicate of ${j.duplicate_of}` : ""}</div>
                  </td>
                  <td><Meter value={j.fit} /></td>
                  <td><RiskBadge level={j.risk_level} scam={j.is_scam} injection={j.injection} /></td>
                  <td><ActionBadge action={j.action} status={j.status} /></td>
                  <td>{fmtMoney(j.price, j.bid_type === "hourly")}</td>
                  <td>{j.connects ?? "—"}</td>
                  <td className="row" style={{ flexWrap: "nowrap" }}>
                    {j.status === "awaiting_approval" && (
                      <Link className="btn" href={`/studio/${j.job_id}`} onClick={(e) => e.stopPropagation()}>Review</Link>
                    )}
                    {!["skipped", "dismissed", "submitted"].includes(j.status) && (
                      <button className="ghost" onClick={(e) => skip(e, j.job_id)} aria-label={`Skip ${j.title}`}>Skip</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
