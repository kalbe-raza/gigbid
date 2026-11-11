/* API fixtures for UI tests. Shapes follow the generated OpenAPI types (src/lib/api/schema.d.ts), so a contract
 * change in FastAPI breaks these tests at type-check time instead of silently. */
import type { Page, Route } from "@playwright/test";
import type { components } from "../../src/lib/api/schema";

type S = components["schemas"];

export const user: S["User"] = { user_id: "u-test", email: "t@gigbid.test", display_name: "Test User", corpus: "fullstack", onboarded: true, persona: "fullstack" };

export const inbox: S["InboxItem"][] = [
  { job_id: "job-1", title: "Build a React booking dashboard", category: "Full Stack Development", status: "awaiting_approval", fit: 0.78, risk: 0.12, risk_level: "low", is_scam: false, injection: false, action: "bid", price: 820, bid_type: "fixed", connects: 4, thread_id: "bid-u-test-job-1" },
  { job_id: "job-2", title: "Unpaid trial: complete a production dashboard", category: "Data Analytics", status: "skipped", fit: 0.4, risk: 0.9, risk_level: "high", is_scam: true, injection: false, action: "skip", thread_id: "bid-u-test-job-2" },
];

const letter = "You need a booking dashboard your front desk can trust. I built a clinic booking platform with reminders and waitlists that cut no-shows by 27% [case:clinicq].\n\nWhat does your current booking flow look like?";

export const studio: S["StudioPayload"] = {
  thread_id: "bid-u-test-job-1", status: "awaiting_approval", pending: true,
  approval: {
    kind: "proposal", thread_id: "bid-u-test-job-1",
    draft: { version: 2, cover_letter: letter, source: "verifier", word_count: 36, screening_answers: [] },
    claims: [{ text: "I built a clinic booking platform with reminders and waitlists that cut no-shows by 27% [case:clinicq].", status: "supported", action: "kept",
      evidence: [{ doc_id: "case:clinicq", title: "Clinic appointment booking platform", snippet: "Built online booking with reminders, waitlists... no-shows fell by 27% in three months" }] }],
    dry_run: { connects_cost: 4, connects_balance_after: 76, connects_surcharges: [], policy_warnings: [],
      would_send: { job_id: "job-1", cover_letter: letter.replace(" [case:clinicq]", " (portfolio case study: Clinic appointment booking platform)"), bid_amount: 820, bid_type: "fixed", screening_answers: [] } },
  },
  drafts: [{ version: 2, cover_letter: letter, source: "verifier", word_count: 36, screening_answers: [] }],
  critiques: [{ approve: true, score: 8, issues: [], instructions: "" }],
  verification: [{ total_claims: 1, unsupported_before: 1, unsupported_after: 0, claims: [] }],
  compliance: [{ allowed: true, violations: [], checked_by: ["patterns", "groq:openai/gpt-oss-safeguard-20b"], final_text: letter }],
};

export const healthy: S["Health"] = { api: true, marketplace: true, portfolio: true, workspace: true, degraded: false, queued_jobs: 0 };
export const degraded: S["Health"] = { api: true, marketplace: false, portfolio: true, workspace: true, degraded: true,
  message: "Marketplace unavailable - new work is queued and will resume automatically.", queued_jobs: 3 };

export async function mockApi(page: Page, overrides: Record<string, (route: Route) => Promise<void> | void> = {}) {
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const key = `${route.request().method()} ${url.pathname}`;
    for (const [pattern, handler] of Object.entries(overrides)) {
      if (new RegExp(`^${pattern}$`).test(key)) return handler(route);
    }
    if (key === "GET /api/auth/me") return json(route, user);
    if (key === "GET /api/health") return json(route, healthy);
    if (key === "GET /api/jobs") return json(route, inbox);
    if (key === "GET /api/proposals/job-1") return json(route, studio);
    if (key.startsWith("GET /api/runs/") && key.endsWith("/stream")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: status\ndata: {\"status\":\"awaiting_approval\"}\n\n" });
    return json(route, {}, 404);
  });
}
