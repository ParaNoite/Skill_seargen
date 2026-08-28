import { createServer } from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const root = normalize(join(import.meta.dirname, '..', 'frontend', 'ted-games'));
const out = normalize(join(import.meta.dirname, '..', '.agent-lab', 'supervisor', 'supervision-20260817-025522-4e2921', 'captures', 'theme-001', 'interactive-success'));
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };

function startServer() {
  const server = createServer(async (request, response) => {
    let requestPath = decodeURIComponent(request.url.split('?')[0]);
    if (requestPath.endsWith('/')) requestPath += 'index.html';
    const target = normalize(join(root, requestPath));
    if (!target.startsWith(root)) return response.writeHead(403).end();
    try {
      const body = await readFile(target);
      response.writeHead(200, { 'content-type': types[extname(target)] || 'application/octet-stream' });
      response.end(body);
    } catch {
      response.writeHead(404).end();
    }
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

const server = await startServer();
const browser = await chromium.launch({
  headless: true,
});
const context = await browser.newContext({ viewport: { width: 1100, height: 760 } });
await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
await mkdir(out, { recursive: true });
const result = { url: `http://127.0.0.1:${server.address().port}`, checks: [], console_errors: [] };

try {
  const desktop = await context.newPage();
  desktop.on('pageerror', error => result.console_errors.push(String(error)));
  await desktop.goto(`${result.url}/skill-3d/`);
  await desktop.waitForFunction(() => window.__runner3d?.renderer);
  await desktop.waitForTimeout(300);
  const threeState = await desktop.evaluate(() => ({
    status: window.__runner3d.getState().status,
    canvas: [window.__runner3d.renderer.domElement.width, window.__runner3d.renderer.domElement.height],
    pixels: window.__runner3d.renderer.domElement.toDataURL().length,
  }));
  await desktop.screenshot({ path: join(out, 'skill-3d-desktop.png'), fullPage: true });
  await desktop.keyboard.press('ArrowLeft');
  const moved = await desktop.evaluate(() => window.__runner3d.getState().lane);
  await desktop.waitForFunction(() => window.__runner3d.getState().status === 'lost', { timeout: 3500 });
  await desktop.locator('#restart-btn').click();
  const restarted = await desktop.evaluate(() => window.__runner3d.getState().status);
  const beforeResize = await desktop.evaluate(() => [window.__runner3d.renderer.domElement.width, window.__runner3d.renderer.domElement.height]);
  await desktop.setViewportSize({ width: 700, height: 500 });
  await desktop.waitForTimeout(150);
  const afterResize = await desktop.evaluate(() => [window.__runner3d.renderer.domElement.width, window.__runner3d.renderer.domElement.height]);
  result.checks.push({ name: '3D桌面启动/非空画布/键盘移动/碰撞/重启/resize', threeState, moved, restarted, beforeResize, afterResize });

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 740 }, hasTouch: true });
  const mobile = await mobileContext.newPage();
  mobile.on('pageerror', error => result.console_errors.push(String(error)));
  await mobile.goto(`${result.url}/skill-3d/`);
  await mobile.waitForFunction(() => window.__runner3d);
  await mobile.locator('#right-zone').tap();
  const touchLane = await mobile.evaluate(() => window.__runner3d.getState().lane);
  await mobile.screenshot({ path: join(out, 'skill-3d-mobile.png'), fullPage: true });
  result.checks.push({ name: '3D移动端触控', touchLane });
  await mobileContext.close();

  const baseline = await context.newPage({ viewport: { width: 1100, height: 760 } });
  baseline.on('pageerror', error => result.console_errors.push(String(error)));
  await baseline.goto(`${result.url}/baseline-2d/`);
  await baseline.waitForFunction(() => window.__runner2d);
  await baseline.screenshot({ path: join(out, 'baseline-2d-desktop.png'), fullPage: true });
  await baseline.locator('#restart').click();
  await baseline.keyboard.press('ArrowRight');
  const baselineState = await baseline.evaluate(() => ({ status: window.__runner2d.getState().status, lane: window.__runner2d.getState().lane, pixels: document.querySelector('#game').toDataURL().length }));
  result.checks.push({ name: '2D启动/键盘移动/重启/非空画布', baselineState });
} finally {
  await context.tracing.stop({ path: join(out, 'ted-games.trace.zip') });
  await context.close();
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}

await writeFile(join(out, 'validation.json'), JSON.stringify(result, null, 2), 'utf8');
console.log(JSON.stringify(result, null, 2));
if (result.console_errors.length) process.exitCode = 1;
