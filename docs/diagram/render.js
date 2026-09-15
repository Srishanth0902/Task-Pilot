// Rasterise docs/architecture-diagram.svg to a 2x PNG using Playwright's Chromium.
//   node docs/diagram/render.js
// Fonts come from Google Fonts at render time; the PNG is self-contained.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const docs = path.resolve(__dirname, '..');
const svg = fs.readFileSync(path.join(docs, 'architecture-diagram.svg'), 'utf8');

const html = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>html,body{margin:0;padding:0;background:#F4F5F2}svg{display:block}</style>
</head><body>${svg}</body></html>`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1900, height: 1160 },
    deviceScaleFactor: 2,
  });
  await page.setContent(html, { waitUntil: 'networkidle' });
  try { await page.evaluate(() => document.fonts.ready); } catch (e) {}
  const out = path.join(docs, 'architecture-diagram.png');
  await page.locator('svg').screenshot({ path: out });
  await browser.close();
  console.log('wrote ' + out);
})();
