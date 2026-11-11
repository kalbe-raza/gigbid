"use client";
import { useEffect, useState } from "react";
import { Empty, ErrorState, Loading } from "@/components/States";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import { useLoad } from "@/lib/hooks";

export default function Settings() {
  const s = useLoad(() => api.GET("/api/settings").then(unwrap), []);
  const m = useLoad(() => api.GET("/api/memory").then(unwrap), []);
  const [form, setForm] = useState<Schemas["SettingsOut"] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (s.data) setForm({ ...s.data, saved_searches: s.data.saved_searches ?? [], connected_accounts: s.data.connected_accounts ?? [], budgets: s.data.budgets ?? {} }); }, [s.data]);

  if (s.loading && !s.data) return <Loading />;
  if (s.error) return <ErrorState error={s.error} onRetry={s.reload} />;
  if (!form) return null;
  const searches = form.saved_searches ?? [];
  const accounts = form.connected_accounts ?? [];

  async function save() {
    setErr(null);
    try {
      await api.PUT("/api/settings", { body: { profile: form!.profile, autonomy: form!.autonomy, weekly_connects_budget: form!.weekly_connects_budget, saved_searches: form!.saved_searches } }).then(unwrap);
      setSaved("Saved.");
      setTimeout(() => setSaved(null), 2500);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <div className="stack" style={{ maxWidth: 900 }}>
      <div className="head"><div><h1>Settings</h1></div><button className="primary" onClick={save}>Save settings</button></div>
      {saved && <div className="banner info" role="status">{saved}</div>}
      {err && <div className="banner bad" role="alert">{err}</div>}
      <section className="panel">
        <h2>Autonomy</h2>
        <div className="field">
          <label htmlFor="autonomy">What may GigBid do without asking?</label>
          <select id="autonomy" value={form.autonomy} onChange={(e) => setForm({ ...form, autonomy: e.target.value as "manual" | "auto_skip_low_fit" })}>
            <option value="manual">Nothing - I decide every skip and every send</option>
            <option value="auto_skip_low_fit">Auto-skip clearly low-fit jobs (never auto-send)</option>
          </select>
        </div>
        <p className="small muted">Sending a proposal or message, or spending Connects, always needs your approval. There is no auto-send setting.</p>
      </section>
      <section className="panel">
        <h2>Rates &amp; budgets</h2>
        <div className="grid3">
          <div className="field"><label htmlFor="rate">Standard rate ($/h)</label><input id="rate" type="number" value={form.profile.hourly_rate} onChange={(e) => setForm({ ...form, profile: { ...form.profile, hourly_rate: Number(e.target.value) } })} /></div>
          <div className="field"><label htmlFor="floor">Floor ($/h)</label><input id="floor" type="number" value={form.profile.hourly_floor} onChange={(e) => setForm({ ...form, profile: { ...form.profile, hourly_floor: Number(e.target.value) } })} /></div>
          <div className="field"><label htmlFor="minp">Minimum project ($)</label><input id="minp" type="number" value={form.profile.min_project} onChange={(e) => setForm({ ...form, profile: { ...form.profile, min_project: Number(e.target.value) } })} /></div>
          <div className="field"><label htmlFor="conn">Weekly Connects budget</label><input id="conn" type="number" value={form.weekly_connects_budget} onChange={(e) => setForm({ ...form, weekly_connects_budget: Number(e.target.value) })} /></div>
        </div>
        <p className="small muted">Declared system budgets: {Object.entries(form.budgets ?? {}).map(([k, v]) => `${k}=${v}`).join(" · ")}</p>
      </section>
      <section className="panel">
        <h2>Saved searches</h2>
        {searches.map((q, i) => (
          <div className="row" key={i} style={{ marginBottom: 8 }}>
            <input aria-label="Keywords" value={q.query ?? ""} placeholder="keywords" onChange={(e) => { const xs = [...searches]; xs[i] = { ...q, query: e.target.value }; setForm({ ...form, saved_searches: xs }); }} style={{ flex: 2 }} />
            <select aria-label="Domain" value={q.domain ?? ""} style={{ flex: 1 }} onChange={(e) => { const xs = [...searches]; xs[i] = { ...q, domain: (e.target.value || null) as never }; setForm({ ...form, saved_searches: xs }); }}>
              <option value="fullstack">full-stack</option><option value="ai_ml">AI/ML</option><option value="data_analyst">data</option>
            </select>
            <input aria-label="Interval seconds" type="number" min={300} value={q.interval_s ?? 900} style={{ flex: 1 }} onChange={(e) => { const xs = [...searches]; xs[i] = { ...q, interval_s: Number(e.target.value) }; setForm({ ...form, saved_searches: xs }); }} />
            <button className="ghost" onClick={() => setForm({ ...form, saved_searches: searches.filter((_, k) => k !== i) })}>Remove</button>
          </div>
        ))}
        <button onClick={() => setForm({ ...form, saved_searches: [...searches, { query: "", domain: form.profile.domain, interval_s: 900, enabled: true }] })}>Add search</button>
      </section>
      <section className="panel">
        <h2>Connected accounts</h2>
        <p>Marketplace mode: <strong>{form.marketplace_mode}</strong></p>
        <p>{accounts.length ? accounts.map((a) => <span key={a} className="badge ok" style={{ marginRight: 6 }}>{a}</span>) : "None"}</p>
        <button onClick={async () => { try { const r = await api.GET("/api/auth/upwork/start").then(unwrap); window.location.href = (r as { authorize_url: string }).authorize_url; } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } }}>Connect Upwork (OAuth)</button>
      </section>
      <section className="panel" data-testid="memory-viewer">
        <h2>What GigBid has learned</h2>
        <p className="small muted">Only knowledge that passed a promotion rule is stored. Delete anything you disagree with.</p>
        {m.loading && !m.data ? <Loading /> : m.error ? <ErrorState error={m.error} /> : (m.data ?? []).length === 0 ? (
          <Empty title="Nothing learned yet">Style rules appear after you make the same kind of edit 3 times on 2+ jobs; outcome insights after 5+ proposals in a category.</Empty>
        ) : (
          <ul style={{ listStyle: "none", padding: 0 }}>
            {(m.data ?? []).map((it) => (
              <li key={`${it.kind}:${it.key}`} className="row" style={{ justifyContent: "space-between", borderBottom: "1px solid var(--line)", padding: "8px 0" }}>
                <span><span className={`badge ${it.kind === "style" ? "info" : "ok"}`}>{it.kind}</span> {it.text}
                  <span className="small muted"> · {Object.entries(it.evidence ?? {}).filter(([k]) => k !== "rule").map(([k, v]) => `${k}: ${Array.isArray(v) ? v.length : v}`).join(", ")}</span></span>
                <button className="danger" onClick={async () => { await api.DELETE("/api/memory/{kind}/{key}", { params: { path: { kind: it.kind, key: it.key } } }); void m.reload(); }} aria-label={`Delete memory ${it.text}`}>Delete</button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="panel">
        <h2>Portfolio</h2>
        <p className="small muted">Own-profile accounts can add work here; it is indexed incrementally.</p>
        <input type="file" accept=".pdf,.md,.zip,.jsonl" aria-label="Upload portfolio file" onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          const fd = new FormData(); fd.append("file", f);
          const r = await fetch("/api/portfolio/upload", { method: "POST", body: fd, credentials: "include" });
          const j = await r.json();
          if (r.ok) setSaved(`Indexed ${j.filename}: ${j.documents} documents`); else setErr(j.detail ?? "upload failed");
        }} />
      </section>
    </div>
  );
}
