import { expect, test } from "@playwright/test";

function watchBrowser(page: import("@playwright/test").Page) {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  return () => expect(errors, errors.join("\n")).toEqual([]);
}

test("Journey A: inspect the STXBP1 cluster and compare against DNM1", async ({ page }) => {
  const assertNoBrowserErrors = watchBrowser(page);
  await page.goto("/d/MONDO%3A0012812?persona=maria");
  await expect(page.locator("#overview")).toContainText("STXBP1");
  await expect(page.locator(".cluster-ranked-list .neighbour-title strong").first()).toBeVisible();
  await expect(page.locator(".radial-map")).toBeVisible();

  const supported = await page.locator(".cluster-ranked-list .neighbour-title strong").allTextContents();
  expect(supported.filter((symbol) => ["SNAP25", "STX1B", "VAMP2"].includes(symbol.trim())).length).toBeGreaterThanOrEqual(2);
  await expect(page.locator(".counterexample-lane .neighbour-title strong")).toHaveText("GNAO1");
  const mapLabels = await page.locator(".radial-map text").evaluateAll((elements) =>
    elements.map((element) => {
      const box = (element as SVGTextElement).getBBox();
      return { x: box.x, y: box.y, right: box.x + box.width, bottom: box.y + box.height };
    }),
  );
  expect(await page.locator(".radial-map").evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThanOrEqual(300);
  for (let left = 0; left < mapLabels.length; left += 1) {
    for (let right = left + 1; right < mapLabels.length; right += 1) {
      const a = mapLabels[left];
      const b = mapLabels[right];
      const separated = a.right <= b.x || b.right <= a.x || a.bottom <= b.y || b.bottom <= a.y;
      expect(separated, `radial labels ${left} and ${right} overlap`).toBe(true);
    }
  }
  await expect(page.locator(".partial-overlap-card")).toHaveCount(5);

  const disclosure = page.getByRole("button", { name: /Show all \(\d+\)/ });
  await expect(disclosure).toBeVisible();
  await disclosure.click();
  expect(await page.locator(".partial-overlap-card").count()).toBeGreaterThan(5);
  await page.getByRole("button", { name: "Show fewer" }).click();
  await expect(page.locator(".partial-overlap-card")).toHaveCount(5);

  const assetSelector = page.getByLabel("Compare assets against");
  await expect(assetSelector.locator("option:checked")).toContainText("STX1B");
  const groupOrder = await assetSelector.locator("optgroup").evaluateAll((groups) =>
    groups.map((group) => group.getAttribute("label")),
  );
  expect(groupOrder).toEqual(["Cluster members", "Partial overlaps", "Other slice diseases"]);
  const dnm1Id = await assetSelector.locator("option").filter({ hasText: /^DNM1/ }).getAttribute("value");
  expect(dnm1Id).toBeTruthy();
  await assetSelector.selectOption(dnm1Id!);
  await expect(page).toHaveURL(new RegExp(`vs=${encodeURIComponent(dnm1Id!)}`));
  await expect(page.locator(".partial-overlap-card")).toHaveCount(6);
  const dnm1Overlap = page.locator(".partial-overlap-card").filter({ hasText: "DNM1" });
  await expect(dnm1Overlap).toContainText("vesicle organization (GO:0016050)");
  await expect(dnm1Overlap).toContainText("excluded by: mechanism layer (M < 0.25)");

  const trialCard = page.locator(".asset-card").filter({ hasText: "NCT06555965" });
  await expect(trialCard).toBeVisible();
  await expect(trialCard.locator(".coverage-score")).toContainText(/\d+% \(IC .+, \d+ of \d+ phenotypes\)/);
  const peopleSelector = page.getByLabel("Compare bridge people against");
  await expect(peopleSelector).toHaveValue(dnm1Id!);
  const ingoCard = page.locator(".person-card").filter({
    has: page.getByRole("heading", { name: "Ingo Helbig" }),
  });
  await expect(ingoCard).toBeVisible();
  await expect(ingoCard.locator(".proving-edges li").first()).toBeVisible();
  assertNoBrowserErrors();
});

test("Journey B: identify FRRS1L as an honest coverage gap", async ({ page }) => {
  const assertNoBrowserErrors = watchBrowser(page);
  await page.goto("/");
  const input = page.getByRole("combobox", { name: "Search diseases and mechanisms" });
  await input.fill("FRRS1L");
  await input.press("Enter");
  await expect(page).toHaveURL(/MONDO%3A0014859/);
  await expect(page.locator("#overview")).toContainText("FRRS1L");
  await expect(page.locator("#overview")).toContainText("MONDO:0014859");
  await expect(page.getByRole("heading", { name: "Honest gap report" })).toBeVisible();
  await expect(page.getByText("No supported neighbour meets the threshold (S ≥ 0.45)")).toBeVisible();
  await expect(page.locator("#cluster")).toHaveCount(0);

  await page.getByRole("link", { name: "Open Shared Path Dossier" }).click();
  await expect(page.getByRole("heading", { name: "What would change this" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Search coverage" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Who shares our characteristics" })).toHaveCount(0);
  assertNoBrowserErrors();
});
