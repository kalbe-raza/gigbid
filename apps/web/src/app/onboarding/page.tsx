"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useUser } from "@/components/Shell";
import { api, unwrap } from "@/lib/api/client";

type Domain = "ai_ml" | "fullstack" | "data_analyst";
const DEFAULTS: Record<Domain, { hourly_rate: number; hourly_floor: number; min_project: number }> = {
  ai_ml: { hourly_rate: 45, hourly_floor: 35, min_project: 300 },
  fullstack: { hourly_rate: 40, hourly_floor: 30, min_project: 250 },
  data_analyst: { hourly_rate: 30, hourly_floor: 22, min_project: 150 },
};

export default function Onboarding() {
  const user = useUser();
  const router = useRouter();
  const initial = (user?.persona && user.persona !== "own" ? user.persona : "fullstack") as Domain;
  const [domain, setDomain] = useState<Domain>(initial);
  const [rates, setRates] = useState(DEFAULTS[initial]);
  const [market, setMarket] = useState<"gigsim" | "upwork_live">("gigsim");
  const [token, setToken] = useState("");
  const [budget, setBudget] = useState(80);
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState<FileList | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const own = user?.persona === "own";

  async function connectUpwork() {
    try {
      const r = await api.GET("/api/auth/upwork/start").then(unwrap);
      window.location.href = (r as { authorize_url: string }).authorize_url;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (own && files) {
        for (const f of Array.from(files)) {
          const fd = new FormData();
          fd.append("file", f);
          const r = await fetch("/api/portfolio/upload", { method: "POST", body: fd, credentials: "include" });
          if (!r.ok) throw new Error(`upload of ${f.name} failed: ${(await r.json()).detail ?? r.status}`);
          setMsg(`Indexed ${f.name}`);
        }
      }
      await api
        .POST("/api/auth/onboarding", {
          body: {
            marketplace: market,
            gigsim_token: token || null,
            profile: { ...rates, domain },
            weekly_connects_budget: budget,
            saved_searches: [{ query, domain, interval_s: 900, enabled: true }],
          },
        })
        .then(unwrap);
      router.replace("/inbox");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="stack" style={{ maxWidth: 760 }} aria-label="Onboarding">
      <div>
        <h1>Set up GigBid</h1>
        <p className="muted">Four short steps. You can change everything later in Settings.</p>
      </div>
      <section className="panel">
        <h2>1. Portfolio</h2>
        {own ? (
          <>
            <p className="small muted">Upload your CV (PDF), case studies (.md), repositories (.zip) or past proposals (.jsonl).</p>
            <input type="file" multiple accept=".pdf,.md,.zip,.jsonl" onChange={(e) => setFiles(e.target.files)} aria-label="Portfolio files" />
            {msg && <p className="small">{msg}</p>}
          </>
        ) : (
          <p>
            Using the synthetic <strong>{user?.persona}</strong> persona portfolio (CV, case studies, repos, reviews, ~150 past
            proposals with outcomes).
          </p>
        )}
        <div className="field" style={{ marginTop: 10 }}>
          <label htmlFor="domain">Kind of work</label>
          <select id="domain" value={domain} onChange={(e) => { const d = e.target.value as Domain; setDomain(d); setRates(DEFAULTS[d]); }}>
            <option value="fullstack">Full-stack web development</option>
            <option value="ai_ml">AI / machine learning</option>
            <option value="data_analyst">Data analysis</option>
          </select>
        </div>
      </section>
      <section className="panel">
        <h2>2. Marketplace</h2>
        <div className="row">
          <label className="row" style={{ color: "var(--ink)" }}>
            <input type="radio" name="m" style={{ width: "auto" }} checked={market === "gigsim"} onChange={() => setMarket("gigsim")} /> GigSim (simulator)
          </label>
          <label className="row" style={{ color: "var(--ink)" }}>
            <input type="radio" name="m" style={{ width: "auto" }} checked={market === "upwork_live"} onChange={() => setMarket("upwork_live")} /> Upwork (official MCP, OAuth)
          </label>
        </div>
        {market === "gigsim" ? (
          <div className="field">
            <label htmlFor="token">GigSim token (leave empty for the demo account)</label>
            <input id="token" value={token} onChange={(e) => setToken(e.target.value)} />
          </div>
        ) : (
          <button type="button" onClick={connectUpwork}>Connect Upwork account</button>
        )}
      </section>
      <section className="panel">
        <h2>3. Rates</h2>
        <div className="grid3">
          {(["hourly_rate", "hourly_floor", "min_project"] as const).map((k) => (
            <div className="field" key={k}>
              <label htmlFor={k}>{k === "hourly_rate" ? "Standard rate ($/h)" : k === "hourly_floor" ? "Floor ($/h)" : "Minimum project ($)"}</label>
              <input id={k} type="number" min={1} value={rates[k]} onChange={(e) => setRates({ ...rates, [k]: Number(e.target.value) })} />
            </div>
          ))}
        </div>
      </section>
      <section className="panel">
        <h2>4. Filters &amp; budget</h2>
        <div className="grid2">
          <div className="field">
            <label htmlFor="query">Saved search keywords (optional)</label>
            <input id="query" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. react dashboard" />
          </div>
          <div className="field">
            <label htmlFor="budget">Weekly Connects budget</label>
            <input id="budget" type="number" min={0} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
          </div>
        </div>
      </section>
      {err && <div className="banner bad" role="alert">{err}</div>}
      <div className="row">
        <button className="primary" type="submit" disabled={busy} data-testid="finish-onboarding">
          {busy ? "Saving…" : "Finish and start scouting"}
        </button>
      </div>
    </form>
  );
}
