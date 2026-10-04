const { chromium } = require('@playwright/test');
const fs = require('fs');
const dur = JSON.parse(fs.readFileSync(process.env.HOME + '/vo/dur.json'));
const BASE = 'http://localhost:3000';
(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, recordVideo: { dir: process.env.HOME + '/vo/video', size: { width: 1280, height: 800 } } });
  const page = await context.newPage();
  const t0 = Date.now();
  page.setDefaultTimeout(60000);
  const marks = {};
  const sleep = (ms) => page.waitForTimeout(ms);
  const smooth = async (sel, block = 'start') => { await page.locator(sel).first().evaluate((el, b) => el.scrollIntoView({ behavior: 'smooth', block: b }), block); await sleep(1200); };
  const scrollBy = async (y) => { await page.evaluate((y) => window.scrollBy({ top: y, behavior: 'smooth' }), y); await sleep(1000); };
  async function seg(key, fn) {
    const start = Date.now(); marks[key] = (start - t0) / 1000;
    await fn();
    const remain = start + dur[key] * 1000 + 700 - Date.now();
    if (remain > 0) await sleep(remain);
    console.log(key, marks[key].toFixed(2), ((Date.now() - start) / 1000).toFixed(2), 'audio', dur[key]);
  }
  await page.goto(BASE + '/?persona=maria');
  await page.locator('#disease-search').waitFor();
  await sleep(800);
  await seg('s1', async () => {});
  await seg('s2', async () => {
    await page.locator('#disease-search').click();
    await page.locator('#disease-search').pressSequentially('STXBP1', { delay: 140 });
    await page.locator('.resolved-line').waitFor();
    await sleep(900);
    await page.locator('.resolved-line').click();
    await page.getByRole('heading', { name: /developmental and epileptic encephalopathy, 4/ }).waitFor();
    await page.getByText('Patient communities', { exact: false }).first().waitFor();
    await sleep(6000);
    await smooth('text=Patient communities', 'center');
  });
  await seg('s3a', async () => {
    await page.locator('.neighbour-card').first().waitFor();
    await smooth('#cluster');
    await sleep(2500);
    await smooth('text=Supported neighbours', 'start');
    const stx = page.locator('.neighbour-button', { hasText: 'STX1B' }).first();
    await stx.click(); await sleep(2000);
    const ev = page.locator('.neighbour-card', { hasText: 'STX1B' }).first().locator('a[aria-label^="Evidence"]').first();
    await ev.click();
    await page.locator('#edge-inspector-title').waitFor(); await sleep(5000);
    await page.locator('[aria-label="Close edge inspector"]').click(); await sleep(500);
  });
  await seg('s3b', async () => {
    await smooth('.counterexample-lane', 'center');
    await page.locator('.counterexample-lane .neighbour-button').first().click();
    await sleep(800); await smooth('.counterexample-lane', 'center');
  });
  await seg('s3c', async () => {
    await smooth('.partial-overlap-lane', 'start');
    const btn = page.getByRole('button', { name: /Show all \(/ });
    if (await btn.count()) { await btn.click(); await sleep(700); }
    await smooth('#partial-dnm1', 'center');
    await page.locator('#partial-dnm1 button').first().click().catch(() => {});
    await sleep(600); await smooth('#partial-dnm1', 'center');
  });
  await seg('s4', async () => {
    await smooth('#assets');
    await page.locator('select[aria-label="Compare assets against"]').selectOption('MONDO:0957248');
    await sleep(1500);
    const card = page.locator('.asset-card', { hasText: 'NCT06555965' }).first();
    await card.waitFor();
    await card.evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    await sleep(9000);
    await scrollBy(350);
  });
  await seg('s5', async () => {
    await smooth('#people');
    const card = page.locator('article, div').filter({ has: page.getByRole('heading', { name: 'Ingo Helbig' }) }).last();
    await page.getByText('Ingo Helbig').first().waitFor();
    await sleep(5000);
    await page.locator('details.why-person summary').first().click();
    await sleep(800); await scrollBy(250);
  });
  await seg('s6a', async () => {
    await smooth('text=Open Shared Path Dossier', 'center');
    await sleep(800);
    await page.getByRole('link', { name: 'Open Shared Path Dossier' }).first().click();
    await page.getByRole('heading', { name: 'Shared Path Dossier' }).waitFor();
    await page.getByText(/GPT-5 · generated/).waitFor({ timeout: 90000 });
    await sleep(4000);
    const fn = page.locator('[data-testid="footnote"]').nth(4);
    await fn.evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' })); await sleep(1000);
    await fn.hover(); await sleep(3500);
    await fn.click(); await sleep(500);
    if (await page.locator('#edge-inspector-title').count()) { await sleep(3500); await page.locator('[aria-label="Close edge inspector"]').click().catch(() => {}); }
    await scrollBy(400);
  });
  await seg('s6b', async () => {
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await sleep(800);
    await page.getByRole('radio', { name: 'Dr. Osei' }).click();
    await page.getByText(/Persona: osei/).waitFor();
    await page.getByText(/GPT-5 · generated/).waitFor({ timeout: 90000 });
    await sleep(3500); await scrollBy(450); await sleep(2500); await scrollBy(450);
  });
  await seg('s7', async () => {
    await page.goto(BASE + '/?persona=maria');
    await page.locator('#disease-search').click();
    await page.locator('#disease-search').pressSequentially('FRRS1L', { delay: 140 });
    await page.locator('.resolved-line').waitFor(); await sleep(500);
    await page.keyboard.press('Enter');
    await page.getByRole('heading', { name: 'Honest gap report' }).waitFor();
    await sleep(1500);
    await smooth('#coverage');
    await sleep(9000);
    await smooth('text=Nearest leads', 'start');
    await sleep(4000);
    await smooth('text=What would change this', 'start');
  });
  await seg('s8', async () => { await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); });
  fs.writeFileSync(process.env.HOME + '/vo/marks.json', JSON.stringify(marks));
  const v = page.video();
  await context.close();
  console.log('video', await v.path());
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
