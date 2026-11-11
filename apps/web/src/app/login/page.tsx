"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, unwrap } from "@/lib/api/client";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [persona, setPersona] = useState<"fullstack" | "ai_ml" | "data_analyst" | "own">("fullstack");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const u =
        mode === "login"
          ? await api.POST("/api/auth/login", { body: { email, password } }).then(unwrap)
          : await api.POST("/api/auth/register", { body: { email, password, display_name: name, persona } }).then(unwrap);
      router.replace(u.onboarded ? "/inbox" : "/onboarding");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 420, margin: "8vh auto", padding: 16 }}>
      <div className="brand" style={{ fontSize: "1.6rem", padding: 0 }}>
        Gig<span style={{ color: "var(--accent)" }}>Bid</span>
      </div>
      <p className="muted">Finds jobs, judges them, drafts proposals grounded in your portfolio. You approve everything.</p>
      <form className="panel" onSubmit={submit} aria-label={mode === "login" ? "Sign in" : "Create account"}>
        <h1>{mode === "login" ? "Sign in" : "Create an account"}</h1>
        {mode === "register" && (
          <div className="field">
            <label htmlFor="name">Display name</label>
            <input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
        )}
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password} onChange={(e) => setPassword(e.target.value)} minLength={6} required />
        </div>
        {mode === "register" && (
          <div className="field">
            <label htmlFor="persona">Portfolio</label>
            <select id="persona" value={persona} onChange={(e) => setPersona(e.target.value as typeof persona)}>
              <option value="fullstack">Demo persona: full-stack web developer</option>
              <option value="ai_ml">Demo persona: AI/ML engineer</option>
              <option value="data_analyst">Demo persona: data analyst</option>
              <option value="own">My own profile (upload in onboarding)</option>
            </select>
          </div>
        )}
        {err && (
          <div className="banner bad" role="alert">
            {err}
          </div>
        )}
        <div className="row">
          <button className="primary" type="submit" disabled={busy} data-testid="submit-auth">
            {mode === "login" ? "Sign in" : "Create account"}
          </button>
          <button type="button" className="ghost" onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "New here? Create an account" : "I have an account"}
          </button>
        </div>
      </form>
      <p className="small muted" style={{ marginTop: 12 }}>
        Personas and GigSim are synthetic. Live Upwork mode only ever creates drafts you confirm on Upwork.
      </p>
    </main>
  );
}
