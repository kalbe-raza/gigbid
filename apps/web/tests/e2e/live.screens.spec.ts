/* Live run against a real stack (E2E_LIVE=1, REVIEWER_EMAIL/REVIEWER_PASSWORD). Captures the screenshots used in docs.
 * Skipped in CI, which has no LLM keys. */
import { expect, test } from "@playwright/test";

const live = !!process.env.E2E_LIVE;
test.skip(!live, "set E2E_LIVE=1 to run against a real backend");

test("live: sign in, inbox, job detail, studio, settings", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/login");
  await page.getByLabel("Email").fill(process.env.REVIEWER_EMAIL || "");
  await page.getByLabel("Password").fill(process.env.REVIEWER_PASSWORD || "");
  await page.getByTestId("submit-auth").click();
  await expect(page).toHaveURL(/\/inbox/);
  await expect(page.getByTestId("inbox-table")).toBeVisible({ timeout: 30_000 });
  const dir = process.env.SHOTS_DIR || "../../docs/images";
  await page.screenshot({ path: `${dir}/inbox.png`, fullPage: true });
  const review = page.getByRole("link", { name: "Review" }).first();
  if (await review.count()) {
    await review.click();
    await expect(page.getByTestId("approval-panel")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("claim").first().hover();
    await page.screenshot({ path: `${dir}/studio.png`, fullPage: true });
    await page.goBack();
  }
  await page.getByTestId("inbox-row").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${dir}/job_detail.png`, fullPage: true });
  for (const p of ["pipeline", "runs", "analytics", "settings", "messages"]) {
    await page.goto(`/${p}`);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${dir}/${p}.png`, fullPage: true });
  }
});
