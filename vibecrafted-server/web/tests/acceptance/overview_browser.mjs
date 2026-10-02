#!/usr/bin/env node
// Headless geometry/navigation acceptance over real SSR fixtures + production CSS.
// Generate fixtures with VC_OVERVIEW_FIXTURE_DIR=<dir> cargo test -p
// vibecrafted-server-web --features ssr overview_navigation_hub, then:
// node overview_browser.mjs <dir> [--json <receipt>]. No desktop capture.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const directory = resolve(process.argv[2]);
const output = process.argv.includes("--json")
  ? process.argv[process.argv.indexOf("--json") + 1]
  : null;
const fixtures = Object.fromEntries(
  ["full", "empty", "unavailable"].map((name) => [
    name,
    readFileSync(join(directory, `${name}.html`), "utf8"),
  ]),
);
let active = "full";
let healthMode = "stale";
const server = createServer((request, response) => {
  const path = new URL(request.url, "http://fixture").pathname;
  if (path.startsWith("/api/")) {
    response.setHeader("Content-Type", "application/json");
    if (healthMode === "unavailable") {
      response.writeHead(503);
      response.end("{}");
      return;
    }
    const body =
      path === "/api/health"
        ? { status: "healthy", version: "fixture" }
        : path === "/api/control/caretaker"
          ? healthMode === "unpublished"
            ? { published: false }
            : {
                published: true,
                stale: true,
                age_seconds: 180,
                snapshot: {
                  verdict: { header: "Needs attention" },
                  server: {
                    generation: "/fixture/" + "long-generation/".repeat(30),
                  },
                },
              }
          : {};
    response.end(JSON.stringify(body));
    return;
  }
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  if (path === "/") response.end(fixtures[active]);
  else if (path.startsWith("/fonts/")) {
    response.writeHead(204);
    response.end();
  } else
    response.end(
      `<!doctype html><body data-destination="${path}">Fixture destination</body>`,
    );
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const profile = mkdtempSync(join(tmpdir(), "vc-overview-acceptance-"));
const chromium =
  process.env.CHROMIUM || "/Applications/Chromium.app/Contents/MacOS/Chromium";
const chrome = spawn(
  chromium,
  [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
let ws;
const record = { checks: [], navigation: [], exceptions: [] };
try {
  const endpoint = await new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(
      () => reject(new Error("Chromium startup timed out")),
      20000,
    );
    chrome.stderr.on("data", (chunk) => {
      buffer += chunk;
      const match = buffer.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    chrome.on("error", reject);
  });
  ws = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });
  let nextId = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject, timer } = pending.get(message.id);
      pending.delete(message.id);
      clearTimeout(timer);
      message.error
        ? reject(new Error(message.error.message))
        : resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown")
      record.exceptions.push(message.params.exceptionDetails);
  };
  const send = (method, params = {}, sessionId) =>
    new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, 10000);
      pending.set(id, { resolve, reject, timer });
      ws.send(JSON.stringify({ id, method, params, sessionId }));
    });
  const { targetId } = await send("Target.createTarget", {
    url: "about:blank",
  });
  const { sessionId } = await send("Target.attachToTarget", {
    targetId,
    flatten: true,
  });
  const cdp = (method, params = {}) => send(method, params, sessionId);
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  const evaluate = async (expression) => {
    const result = await cdp("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails)
      throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const waitFor = async (expression) => {
    for (let i = 0; i < 100; i++) {
      if (await evaluate(expression)) return;
      await new Promise((resolve) => setTimeout(resolve, 30));
    }
    throw new Error(`Condition timed out: ${expression}`);
  };
  const load = async (native) => {
    await cdp("Page.navigate", {
      url: `${origin}/?fixture=${active}&native=${native}`,
    });
    await waitFor(
      "document.querySelector('.overview-routes') && document.getElementById('overview-server-health').textContent !== 'Reading readiness…'",
    );
    await evaluate(
      `document.documentElement.toggleAttribute('data-native-shell', ${native})`,
    );
    await evaluate(
      "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
  };
  for (const fixture of ["full", "empty", "unavailable"]) {
    active = fixture;
    for (const [width, height, native] of [
      [800, 600, false],
      [1200, 800, false],
      [800, 600, true],
      [1200, 800, true],
      [340, 500, true],
    ]) {
      await cdp("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: false,
      });
      await load(native);
      const geometry = await evaluate(`(() => {
        const rect = node => { const r = node.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}; };
        const sections = [...document.querySelectorAll('.overview-head,.overview-stats,.overview-health,.overview-dashboard .control-panel')].map(node => ({label:node.getAttribute('aria-label') || node.className, ...rect(node)}));
        const regions = [...document.querySelectorAll('.overview-region')].map(node => ({...rect(node),clientHeight:node.clientHeight,scrollHeight:node.scrollHeight,overflow:getComputedStyle(node).overflowY}));
        const main = document.querySelector('.server-route-main');
        return {sections,regions,liveBadge:document.querySelector('.overview-live .control-panel-head span').textContent.trim(),health:rect(document.querySelector('.overview-health')),doc:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},main:{height:main.clientHeight,scrollHeight:main.scrollHeight},stats:[...document.querySelectorAll('.overview-stats dd')].map(n=>n.textContent.trim()),cards:document.querySelectorAll('.overview-route-card').length};
      })()`);
      assert.ok(
        geometry.doc.width <= width + 1 && geometry.doc.height <= height + 1,
        JSON.stringify({ width, height, geometry }),
      );
      assert.ok(
        geometry.main.scrollHeight <= geometry.main.height + 1,
        "Overview scrolls its entire document",
      );
      assert.equal(geometry.cards, 11);
      assert.equal(
        geometry.liveBadge,
        fixture === "full" ? "8" : fixture === "empty" ? "0" : "Unknown",
      );
      assert.ok(
        geometry.health.height <= 100,
        JSON.stringify({
          message: "Health expanded beyond three compact readings",
          geometry,
        }),
      );
      for (const section of geometry.sections)
        assert.ok(
          section.x >= -1 &&
            section.y >= -1 &&
            section.right <= width + 1 &&
            section.bottom <= height + 1 &&
            section.height > 0,
          JSON.stringify(section),
        );
      for (const region of geometry.regions)
        assert.ok(
          region.height >= 35 && region.overflow === "auto",
          "Essential content region hidden or not independently scrollable",
        );
      assert.deepEqual(
        geometry.stats,
        fixture === "full"
          ? ["8", "2", "9", "3"]
          : fixture === "empty"
            ? ["0", "0", "0", "0"]
            : ["Unknown", "Unknown", "Unknown", "Unknown"],
      );
      await evaluate(
        "document.querySelector('.overview-routes').scrollTop = 10000",
      );
      assert.ok(
        await evaluate(
          "document.querySelector('.overview-route-card[href=\"/about\"]').getBoundingClientRect().bottom <= document.querySelector('.overview-routes').getBoundingClientRect().bottom + 1",
        ),
        "Last route unreachable within navigation region",
      );
      assert.equal(
        await evaluate(
          "document.querySelector('.server-route-main').scrollTop",
        ),
        0,
      );
      record.checks.push({ fixture, width, height, native, ...geometry });
    }
  }
  active = "full";
  for (const mode of ["unavailable", "unpublished", "stale"]) {
    healthMode = mode;
    await load(true);
    await waitFor(
      `document.getElementById('overview-runtime-health').textContent.includes(${JSON.stringify(mode === "unavailable" ? "Unavailable" : mode === "unpublished" ? "Not published" : "Stale")})`,
    );
    const health = await evaluate(
      "document.querySelector('.overview-health').textContent",
    );
    assert.ok(!health.includes("0 USD"));
    record.checks.push({ healthMode: mode, health });
  }
  for (const path of [
    "/runs",
    "/projects",
    "/usage",
    "/skills",
    "/artifacts",
    "/structure",
    "/history",
    "/settings",
    "/diagnostics",
    "/help",
    "/about",
  ]) {
    await load(true);
    await evaluate(
      `document.querySelector('.overview-route-card[href=${JSON.stringify(path)}]').click()`,
    );
    await waitFor(`location.pathname === ${JSON.stringify(path)}`);
    record.navigation.push({
      path,
      actual: await evaluate("location.pathname"),
    });
  }
  assert.equal(record.exceptions.length, 0);
  record.passed = true;
} catch (error) {
  record.passed = false;
  record.error = error.stack;
} finally {
  ws?.close();
  chrome.kill();
  await new Promise((resolve) =>
    chrome.exitCode !== null ? resolve() : chrome.once("exit", resolve),
  );
  server.close();
  rmSync(profile, { recursive: true, force: true });
}
if (output) writeFileSync(output, JSON.stringify(record, null, 2) + "\n");
console.log(
  JSON.stringify(
    {
      passed: record.passed,
      geometryCases: record.checks.length,
      navigation: record.navigation,
      error: record.error,
    },
    null,
    2,
  ),
);
process.exitCode = record.passed ? 0 : 1;
