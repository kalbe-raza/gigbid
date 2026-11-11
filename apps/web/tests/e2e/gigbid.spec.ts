import { expect, test } from "@playwright/test";
import { degraded, mockApi, studio, user } from "./fixtures";

test.describe("GigBid UI", () => {
  test("onboarding: persona, marketplace, rates and budget are saved, then the inbox opens @mobile", async ({ page }) => {
    let onboarded = false;
    let body: Record<string, unknown> = {};
    await mockApi(page, {
      "GET /api/auth/me": (r) => r.fulfill({ json: { ...user, onboarded } }),
      "POST /api/auth/onboarding": async (r) => {
        body = r.request().postDataJSON();
        onboarded = true;
        await r.fulfill({ json: { ...user, onboarded: true } });
      },
    });
    await page.goto("/onboarding");
    await expect(page.getByRole("heading", { name: "Set up GigBid" })).toBeVisible();
    await page.getByLabel("Kind of work").selectOption("ai_ml");
    await page.getByLabel("Floor ($/h)").fill("38");
    await page.getByLabel("Weekly Connects budget").fill("60");
    await page.getByLabel("Saved search keywords (optional)").fill("llm rag");
    await page.getByTestId("finish-onboarding").click();
    await expect(page).toHaveURL(/\/inbox/);
    expect(body).toMatchObject({ marketplace: "gigsim", weekly_connects_budget: 60, profile: { domain: "ai_ml", hourly_floor: 38 } });
    expect((body.saved_searches as { query: string }[])[0].query).toBe("llm rag");
  });

  test("approving a proposal shows the dry-run preview and resumes the graph", async ({ page }) => {
    let sent: Record<string, unknown> | null = null;
    await mockApi(page, {
      "POST /api/approvals/bid-u-test-job-1": async (r) => {
        sent = r.request().postDataJSON();
        await r.fulfill({ json: { thread_id: "bid-u-test-job-1", accepted: true, status: "resuming" } });
      },
    });
    await page.goto("/studio/job-1");
    await expect(page.getByTestId("approval-panel")).toContainText("4 Connects");
    await page.getByText("Exactly what the client will receive").click();
    await expect(page.getByTestId("dry-run-text")).toContainText("portfolio case study: Clinic appointment booking platform");
    // evidence on hover
    await page.getByTestId("claim").first().hover();
    await expect(page.getByRole("tooltip")).toContainText("case:clinicq");
    await page.getByTestId("approve").click();
    await expect(page.getByRole("status")).toContainText("Decision recorded (approve)");
    expect(sent).toMatchObject({ decision: "approve" });
  });

  test("rejecting requires a reason and sends it", async ({ page }) => {
    let sent: Record<string, unknown> | null = null;
    await mockApi(page, {
      "POST /api/approvals/bid-u-test-job-1": async (r) => {
        sent = r.request().postDataJSON();
        await r.fulfill({ json: { thread_id: "bid-u-test-job-1", accepted: true, status: "resuming" } });
      },
    });
    await page.goto("/studio/job-1");
    await page.getByTestId("reject").click();
    await expect(page.getByTestId("confirm-reject")).toBeDisabled();
    await page.getByTestId("reject-reason").fill("budget too low for the scope");
    await page.getByTestId("confirm-reject").click();
    await expect(page.getByRole("status")).toContainText("Decision recorded (reject)");
    expect(sent).toMatchObject({ decision: "reject", reason: "budget too low for the scope" });
  });

  test("editing a draft sends the edited text back for compliance re-check", async ({ page }) => {
    let sent: Record<string, unknown> | null = null;
    await mockApi(page, {
      "POST /api/approvals/bid-u-test-job-1": async (r) => {
        sent = r.request().postDataJSON();
        await r.fulfill({ json: { thread_id: "bid-u-test-job-1", accepted: true, status: "resuming" } });
      },
    });
    await page.goto("/studio/job-1");
    await page.getByTestId("edit").click();
    const box = page.getByTestId("edit-text");
    await expect(box).toHaveValue(/clinic booking platform/);
    await box.fill("Your front desk needs a booking view it can trust. I built a clinic booking platform that cut no-shows by 27% [case:clinicq].\n\nWhen do you want to launch?");
    await page.getByTestId("save-edit").click();
    await expect(page.getByRole("status")).toContainText("Decision recorded (edit)");
    expect(sent).toMatchObject({ decision: "edit" });
    expect(String((sent as unknown as { edited_text: string }).edited_text)).toContain("When do you want to launch?");
  });

  test("degraded mode: the banner says the marketplace is unavailable and work is queued @mobile", async ({ page }) => {
    await mockApi(page, { "GET /api/health": (r) => r.fulfill({ json: degraded }) });
    await page.goto("/inbox");
    const banner = page.getByTestId("degraded-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Marketplace unavailable");
    await expect(banner).toContainText("3 jobs queued");
    await expect(page.getByTestId("inbox-table")).toBeVisible();
  });

  test("keyboard: a claim's evidence opens with Enter", async ({ page }) => {
    await mockApi(page);
    await page.goto("/studio/job-1");
    await page.getByTestId("claim").first().focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("tooltip")).toContainText(studio.approval ? "Supported" : "");
  });
});
