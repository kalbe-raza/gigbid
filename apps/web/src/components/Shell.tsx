"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import { DegradedBanner } from "./DegradedBanner";

type User = Schemas["User"];
const UserCtx = createContext<User | null>(null);
export const useUser = () => useContext(UserCtx);

const NAV = [
  ["/inbox", "Job inbox"],
  ["/pipeline", "Pipeline"],
  ["/messages", "Messages"],
  ["/runs", "Agent runs"],
  ["/analytics", "Analytics"],
  ["/settings", "Settings"],
] as const;
const PUBLIC = ["/login"];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  const isPublic = PUBLIC.includes(path);

  useEffect(() => {
    if (isPublic) {
      setChecked(true);
      return;
    }
    api
      .GET("/api/auth/me")
      .then(unwrap)
      .then((u) => {
        setUser(u);
        if (!u.onboarded && path !== "/onboarding") router.replace("/onboarding");
      })
      .catch(() => router.replace("/login"))
      .finally(() => setChecked(true));
  }, [path, isPublic, router]);

  if (isPublic) return <>{children}</>;
  if (!checked || !user)
    return (
      <div className="state" role="status">
        <span className="spinner" /> Loading GigBid…
      </div>
    );

  const links = NAV.map(([href, label]) => (
    <Link key={href} href={href} aria-current={path.startsWith(href) ? "page" : undefined}>
      {label}
    </Link>
  ));
  return (
    <UserCtx.Provider value={user}>
      <div className="shell">
        <aside className="side" aria-label="Main navigation">
          <div className="brand">
            Gig<span>Bid</span>
          </div>
          <nav className="nav">{links}</nav>
          <div className="small muted" style={{ position: "absolute", bottom: 16, left: 22, right: 12 }}>
            <div>{user.display_name}</div>
            <div>{user.persona === "own" ? "own portfolio" : `persona: ${user.persona}`}</div>
            <button
              className="ghost"
              onClick={async () => {
                await api.POST("/api/auth/logout");
                router.replace("/login");
              }}
            >
              Sign out
            </button>
          </div>
        </aside>
        <div>
          <nav className="topbar" aria-label="Main navigation (mobile)">
            <strong>GigBid</strong>
            {links}
          </nav>
          <main className="main" id="main">
            <DegradedBanner />
            {children}
          </main>
        </div>
      </div>
    </UserCtx.Provider>
  );
}
