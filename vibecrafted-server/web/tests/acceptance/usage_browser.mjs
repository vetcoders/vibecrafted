#!/usr/bin/env node
// Production SSR/CSS/JavaScript interaction acceptance, no screen capture.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
const directory = resolve(process.argv[2]);
const output = process.argv[3];
const now = Date.parse("2026-10-03T12:00:00Z");
const rows = Array.from({ length: 5000 }, (_, i) => ({
  run_id: `run-${i}`,
  agent: i % 2 ? "codex" : "kimi",
  provider: i % 2 ? "openai" : "moonshot",
  model: i % 2 ? "model-a" : "model-b",
  root: "/recorded/project",
  task: "workflow",
  parent_run_id: i % 2 ? "swarm-a" : "swarm-b",
  provider_session_id: `session-${i}`,
  status: i % 17 ? "completed" : "failed",
  exit_code: i % 17 ? 0 : 1,
  recorded_at: new Date(now - (i + 1) * 3600000).toISOString(),
  started_at: new Date(now - (i + 2) * 3600000).toISOString(),
  duration_s: 3600,
  settlement_verdict: "",
  report_path: "/canonical/report.md",
  telemetry_source: "meta",
  tokens: {
    tokens_total: i % 13 ? i * 10 : { value: "unknown" },
    tokens_input: i * 8,
    tokens_output: i * 2,
    tokens_cached_input: 0,
    tokens_cache_write: 0,
    source: "harness_log",
    counting_version: 2,
    input_semantics: "includes_cache",
    events: 1,
    unit: "tokens",
  },
  cost: {
    amount: i % 13 ? i / 1000 : { value: "unknown" },
    currency: "USD",
    source: i % 2 ? "estimated:test" : "provider_reported",
  },
  signals: i === 0 ? ["shared_session: intervals unknown; inspect"] : [],
  duplicate_of: null,
}));
let mode = "full";
const server = createServer((request, response) => {
  const path = new URL(request.url, "http://fixture").pathname;
  if (path.startsWith("/api/")) {
    response.setHeader("Content-Type", "application/json");
    if (mode === "unavailable" && path === "/api/usage") {
      response.writeHead(503);
      response.end(JSON.stringify({ error: "owner offline" }));
      return;
    }
    if (path === "/api/usage")
      response.end(
        JSON.stringify({
          schema: "vibecrafted.usage-report.v1",
          generated_at: new Date(now).toISOString(),
          runs: mode === "empty" ? [] : rows,
        }),
      );
    else if (path.endsWith("/report"))
      response.end(
        JSON.stringify({
          available: true,
          body: `Report evidence for ${path.split("/")[4]}`,
          truncated: false,
        }),
      );
    else if (path.endsWith("/transcript"))
      response.end(
        JSON.stringify({
          available: true,
          body: `Transcript evidence for ${path.split("/")[4]}`,
          truncated: false,
        }),
      );
    else
      response.end(
        JSON.stringify({
          agents: [],
          generated_at: new Date(now).toISOString(),
        }),
      );
    return;
  }
  const file = path.slice(1) || "usage";
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  try {
    response.end(readFileSync(join(directory, file + ".html"), "utf8"));
  } catch (_) {
    response.end("<!doctype html><body>Existing run detail destination</body>");
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const live = process.env.VC_USAGE_LIVE_ORIGIN;
const origin = live || `http://127.0.0.1:${server.address().port}`;
const profile = mkdtempSync(join(tmpdir(), "vc-usage-acceptance-"));
const chrome = spawn(
  process.env.CHROMIUM || "/Applications/Chromium.app/Contents/MacOS/Chromium",
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
const record = { checks: [], exceptions: [] };
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
    for (let i = 0; i < 600; i++) {
      if (await evaluate(expression)) return;
      await new Promise((resolve) => setTimeout(resolve, 30));
    }
    throw new Error(`Condition timed out: ${expression}`);
  };

  const load = async (path = "/usage") => {
    await cdp("Page.navigate", { url: origin + path });
    await waitFor("document.readyState !== 'loading'");
    if (path.startsWith("/usage"))
      await waitFor(
        "document.getElementById('usage-status').textContent.includes('in scope') || document.getElementById('usage-status').textContent.includes('unavailable')",
      );
    await evaluate(
      "document.documentElement.setAttribute('data-native-shell',''); new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))",
    );
  };
  const geometry = async () =>
    evaluate(`(() => {
    const main=document.querySelector('.server-route-main'), shell=document.querySelector('.usage-dashboard') || document.querySelector('.route-page-shell');
    const bounds=n=>{const r=n.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height}};
    return { doc:document.documentElement.scrollHeight, viewport:innerHeight, main:bounds(main), mainScroll:main.scrollHeight, mainHeight:main.clientHeight, shell:shell && bounds(shell), split:document.querySelector('.usage-split') && bounds(document.querySelector('.usage-split')), children:shell && [...shell.children].filter(n=>getComputedStyle(n).display!=='none').map(n=>({tag:n.tagName,cls:n.className,height:n.getBoundingClientRect().height,order:getComputedStyle(n).order,flex:getComputedStyle(n).flex,columns:getComputedStyle(n).gridTemplateColumns})), rows:document.querySelectorAll('#usage-runs-body tr').length };
  })()`);
  if (live) {
    const started = performance.now();
    await cdp("Emulation.setDeviceMetricsOverride", {
      width: 1280,
      height: 800,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await load("/usage?window=all");
    const elapsedMs = Math.round(performance.now() - started);
    const real = await evaluate(
      "fetch('/api/usage?window=all').then(response=>response.json())",
    );
    const measured = real.runs
      .filter(
        (run) =>
          !run.duplicate_of && typeof run.tokens?.tokens_total === "number",
      )
      .reduce((sum, run) => sum + run.tokens.tokens_total, 0);
    assert.equal(measured, real.totals.tokens_total_known);
    assert.equal(
      await evaluate(
        "document.getElementById('usage-total-tokens').textContent.replace(/[^0-9]/g,'')",
      ),
      String(measured),
    );
    assert(
      (
        await evaluate("document.getElementById('usage-status').textContent")
      ).includes(String(real.runs.length) + " canonical records"),
    );
    const g = await geometry();
    assert(g.doc <= 801);
    assert(g.mainScroll <= g.mainHeight + 1);
    assert.equal(g.rows, Math.min(100, real.runs.length));
    const renderedIds = await evaluate(
      "[...document.querySelectorAll('#usage-runs-body tr')].map(row=>row.dataset.runId)",
    );
    const traced = real.runs.find(
      (run) =>
        renderedIds.includes(run.run_id) &&
        typeof run.tokens?.tokens_total === "number" &&
        run.tokens.tokens_total > 0,
    );
    assert(traced, "real measured evidence row");
    await evaluate(
      `document.querySelector('tr[data-run-id="'+${JSON.stringify(traced.run_id)}+'"]').click()`,
    );
    assert(
      (
        await evaluate(
          "document.getElementById('usage-measurement').textContent",
        )
      ).includes(
        new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 }).format(
          traced.tokens.tokens_total,
        ),
      ),
    );
    record.checks.push({
      check: "real run metadata → usage API → selected measurement",
      runId: traced.run_id,
      knownTokens: traced.tokens.tokens_total,
      source: traced.tokens.source,
      providerSessionRecorded: typeof traced.provider_session_id === "string",
    });
    await waitFor(
      "document.getElementById('usage-measurement').textContent.includes('Run:')",
    );
    await waitFor(
      "document.querySelector('[data-inspector-report]').textContent !== 'Reading…'",
    );
    const id = await evaluate(
      "new URLSearchParams(location.search).get('run')",
    );
    const evidence = await evaluate(
      `Promise.all(['report','transcript'].map(type=>fetch('/api/control/runs/'+encodeURIComponent(${JSON.stringify(id)})+'/'+type).then(response=>response.json()).then(body=>({type,available:body.available,truncated:body.truncated,length:body.body?.length||0}))))`,
    );
    record.checks.push({
      check:
        "real canonical inventory through source server and browser, total equality, bounded evidence and fixed canvas",
      canonicalRecords: real.runs.length,
      knownTokens: measured,
      unknownTotals: real.totals.runs_tokens_unknown,
      costs: real.totals.cost_by_source_unit,
      coverage: real.coverage,
      duplicates: real.totals.duplicates_excluded,
      elapsedMs,
      evidence,
      ...g,
    });
  } else {
    for (const [width, height] of [
      [800, 600],
      [1280, 800],
      [1700, 1100],
    ]) {
      await cdp("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: false,
      });
      await load();
      const g = await geometry();
      assert(g.doc <= height + 1, JSON.stringify(g));
      assert(g.mainScroll <= g.mainHeight + 1, JSON.stringify(g));
      assert(g.split.height >= 85, JSON.stringify(g));
      assert(g.split.bottom <= height + 1, JSON.stringify(g));
      const before = await evaluate(
        "document.querySelector('.server-route-main').scrollTop",
      );
      await evaluate(
        "document.querySelector('.usage-table-scroll').scrollTop=10000",
      );
      assert.equal(
        await evaluate(
          "document.querySelector('.server-route-main').scrollTop",
        ),
        before,
      );
      record.checks.push({
        check: "fixed shell and inner-scroll",
        width,
        height,
        ...g,
      });
    }
    await cdp("Emulation.setDeviceMetricsOverride", {
      width: 1280,
      height: 800,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await load();
    const fullTokens = await evaluate(
      "document.getElementById('usage-total-tokens').textContent",
    );
    await evaluate(
      "document.querySelector('.usage-calendar button').focus(); document.querySelector('.usage-calendar button').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))",
    );
    assert(
      (
        await evaluate("document.getElementById('usage-selection').textContent")
      ).includes("range"),
    );
    assert(
      (
        await evaluate("document.getElementById('usage-hover').textContent")
      ).includes("sources:"),
    );
    await evaluate(
      "document.getElementById('usage-clear-selection').click(); document.querySelector('#usage-runs-body tr').click()",
    );
    await waitFor(
      "document.querySelector('[data-inspector-report]').textContent.includes('Report evidence')",
    );
    assert(
      (
        await evaluate(
          "document.getElementById('usage-measurement').textContent",
        )
      ).includes("accepted delivery"),
    );
    const selected = await evaluate(
      "document.querySelector('#usage-runs-body tr').dataset.runId",
    );
    assert(
      (
        await evaluate(
          "document.querySelector('[data-inspector-report]').textContent",
        )
      ).includes(selected),
    );
    assert(
      (
        await evaluate(
          "document.querySelector('[data-inspector-tail]').textContent",
        )
      ).includes(selected),
    );
    await evaluate("document.querySelector('[data-usage-doc=report]').click()");
    assert.equal(
      await evaluate(
        "document.querySelector('[data-usage-doc-panel=report]').hidden",
      ),
      false,
    );
    const inspectorContext = await evaluate("location.href");
    for (const [width, height] of [
      [800, 600],
      [1700, 1100],
      [1280, 800],
    ]) {
      await cdp("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: false,
      });
      await evaluate(
        "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
      );
      const resized = await geometry();
      assert(resized.doc <= height + 1, JSON.stringify(resized));
      assert(
        resized.mainScroll <= resized.mainHeight + 1,
        JSON.stringify(resized),
      );
      assert(resized.split.bottom <= height + 1, JSON.stringify(resized));
      assert.equal(await evaluate("location.href"), inspectorContext);
      assert.equal(
        await evaluate(
          "document.querySelector('[data-usage-doc-panel=report]').hidden",
        ),
        false,
      );
    }
    record.checks.push({
      check:
        "resize with inspector open preserves filters, selected run and active report; compact pane remains reachable",
    });
    const url = await evaluate("location.href");
    await evaluate("document.getElementById('usage-run-detail').click()");
    await waitFor("location.pathname.startsWith('/run/')");
    await evaluate("history.back()");
    await waitFor(
      "document.getElementById('usage-measurement')?.textContent.includes('Run:')",
    );
    assert.equal(await evaluate("location.href"), url);
    assert.equal(
      await evaluate(
        "document.querySelector('[data-usage-doc-panel=report]').hidden",
      ),
      false,
    );
    await evaluate(
      "document.getElementById('usage-close-inspector').click(); document.getElementById('usage-window').value='all';document.getElementById('usage-filter-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))",
    );
    assert.equal(
      await evaluate("document.querySelectorAll('#usage-runs-body tr').length"),
      100,
    );
    assert(
      (
        await evaluate("document.getElementById('usage-run-count').textContent")
      ).includes("5000"),
    );
    await evaluate("document.getElementById('usage-next').click()");
    assert(
      (
        await evaluate("document.getElementById('usage-page').textContent")
      ).startsWith("101"),
    );
    await evaluate(
      "document.querySelector('[data-usage-view=dimensions]').click(); document.querySelector('[data-usage-dim=models]').click(); document.querySelector('#usage-models button').click()",
    );
    assert(
      (
        await evaluate("document.getElementById('usage-selection').textContent")
      ).includes("model-b"),
    );
    assert.equal(
      await evaluate(
        "[...document.querySelectorAll('#usage-runs-body tr')].every(tr=>tr.children[2].textContent==='model-b')",
      ),
      true,
    );
    await evaluate(
      "document.getElementById('usage-clear-selection').click();document.getElementById('usage-window').value='7d';document.getElementById('usage-filter-form').dispatchEvent(new Event('submit',{cancelable:true}));document.querySelector('[data-usage-view=dimensions]').click()",
    );
    assert(
      (
        await evaluate(
          "document.getElementById('usage-comparison').textContent",
        )
      ).includes("Previous equal period"),
    );
    await evaluate(
      "document.querySelector('[data-usage-view=attention]').click();document.querySelector('#usage-signals button').click()",
    );
    assert.equal(
      await evaluate(
        "document.querySelector('#usage-runs-body tr').dataset.runId",
      ),
      "run-0",
    );
    await evaluate(
      "document.getElementById('usage-clear-selection').click();document.getElementById('usage-agent').value='codex';document.getElementById('usage-filter-form').dispatchEvent(new Event('submit',{cancelable:true}))",
    );
    assert.equal(
      await evaluate(
        "[...document.querySelectorAll('#usage-runs-body tr')].every(tr=>tr.children[1].textContent.includes('codex'))",
      ),
      true,
    );
    record.checks.push({
      check:
        "focus, exact drilldown, report/transcript, restored context, 5000-row pagination, model comparison, period comparison, anomaly rule, filters",
      fullTokens,
    });
    await load(
      "/usage?window=custom&from=2026-10-02T12%3A00&to=2026-10-03T12%3A00",
    );
    assert.equal(
      await evaluate("document.querySelectorAll('#usage-runs-body tr').length"),
      24,
    );
    assert.equal(
      await evaluate(
        "document.getElementById('usage-total-tokens').textContent.replace(/[^0-9]/g,'')",
      ),
      "2630",
    );
    const edge = await evaluate(
      "[...document.querySelectorAll('#usage-runs-body tr')].map(row=>row.dataset.runId)",
    );
    assert(edge.includes("run-23"));
    assert(!edge.includes("run-24"));
    await evaluate(
      "document.querySelector('#usage-chart-cost-plot circle').focus();document.querySelector('#usage-chart-cost-plot circle').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))",
    );
    assert(
      (
        await evaluate("document.getElementById('usage-hover').textContent")
      ).includes("API estimate") ||
        (
          await evaluate("document.getElementById('usage-hover').textContent")
        ).includes("Provider report"),
    );
    await evaluate("document.getElementById('usage-clear-selection').click()");
    const rect = await evaluate(
      "(()=>{const r=document.querySelector('#usage-chart-cost-plot svg').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}})()",
    );
    const startX = rect.x + rect.width * 0.2,
      endX = rect.x + rect.width * 0.8,
      y = rect.y + 4;
    await cdp("Input.dispatchMouseEvent", {
      type: "mousePressed",
      x: startX,
      y,
      button: "left",
      buttons: 1,
      clickCount: 1,
    });
    await cdp("Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x: endX,
      y,
      button: "left",
      buttons: 1,
    });
    await cdp("Input.dispatchMouseEvent", {
      type: "mouseReleased",
      x: endX,
      y,
      button: "left",
      buttons: 0,
      clickCount: 1,
    });
    const range = await evaluate(
      "JSON.parse(new URLSearchParams(location.search).get('selection'))",
    );
    assert.equal(range.kind, "range");
    assert(range.to > range.from);
    const ids = await evaluate(
      "[...document.querySelectorAll('#usage-runs-body tr')].map(row=>row.dataset.runId)",
    );
    assert(ids.length > 0);
    assert(
      ids.every((id) => {
        const row = rows.find((row) => row.run_id === id);
        return (
          Date.parse(row.recorded_at) >= range.from &&
          Date.parse(row.recorded_at) < range.to &&
          JSON.stringify([row.cost.source, row.cost.currency]) ===
            range.series &&
          typeof row.cost.amount === "number"
        );
      }),
    );
    await load(
      "/usage?window=7d&project=%2Frecorded%2Fproject&task=workflow&parent=swarm-a&provider=openai&model=model-a&outcome=failed",
    );
    const grouped = await evaluate(
      "[...document.querySelectorAll('#usage-runs-body tr')].map(row=>row.dataset.runId)",
    );
    assert(grouped.length > 0);
    assert(
      grouped.every((id) => {
        const i = Number(id.slice(4));
        return i % 2 === 1 && i % 17 === 0;
      }),
    );
    record.checks.push({
      check:
        "UTC boundary, unknown completeness, cost focus, pointer range selection, project/skill/parent/provider/model/outcome filters",
      records: 24,
      knownTokens: 2630,
    });
    mode = "empty";
    await load();
    assert.equal(
      await evaluate("document.querySelectorAll('#usage-runs-body tr').length"),
      0,
    );
    assert(
      (
        await evaluate(
          "document.getElementById('usage-total-tokens').textContent",
        )
      ).includes("no records"),
    );
    mode = "unavailable";
    await load();
    assert(
      (
        await evaluate("document.getElementById('usage-status').textContent")
      ).includes("unavailable"),
    );
    mode = "full";
    for (const name of [
      "console",
      "workspaces",
      "sessions",
      "agents",
      "lifecycle",
      "activity",
      "aicx",
      "frame",
      "guide",
      "runs",
      "projects",
      "history",
      "skills",
      "settings",
      "diagnostics",
      "help",
      "about",
      "structure",
      "transcripts",
      "run-detail",
    ]) {
      await cdp("Emulation.setDeviceMetricsOverride", {
        width: 800,
        height: 600,
        deviceScaleFactor: 1,
        mobile: false,
      });
      await load("/" + name);
      const g = await geometry();
      assert(g.doc <= 601, name + JSON.stringify(g));
      assert(g.mainScroll <= g.mainHeight + 1, name + JSON.stringify(g));
      const count = await evaluate(
        "document.querySelectorAll('.route-studio-panel').length",
      );
      if (count) {
        await evaluate(
          "document.querySelector('.route-panel-toolbar select').value=String(document.querySelectorAll('.route-studio-panel').length-1);document.querySelector('.route-panel-toolbar select').dispatchEvent(new Event('change'))",
        );
        assert.equal(
          await evaluate(
            "[...document.querySelectorAll('.route-studio-panel')].filter(n=>!n.hidden).length",
          ),
          1,
        );
        await evaluate(
          "document.querySelector('.route-studio-panel:not([hidden])').insertAdjacentHTML('beforeend','<div style=height:2000px>Inner document end</div>');document.querySelector('.route-studio-panel:not([hidden])').scrollTop=10000",
        );
        assert.equal(
          await evaluate(
            "document.querySelector('.server-route-main').scrollTop",
          ),
          0,
        );
      }
      record.checks.push({
        check: "shared route fixed canvas and switchable content",
        name,
        panels: count,
        ...g,
      });
    }
    for (const [width, height] of [
      [800, 600],
      [1280, 800],
      [1700, 1100],
    ]) {
      await cdp("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: false,
      });
      await load("/scaffold");
      await waitFor(
        "document.querySelectorAll('.artifact-panel.is-active').length === 1",
      );
      const size = await evaluate(
        "({doc:document.documentElement.scrollHeight,viewport:innerHeight,main:document.querySelector('.server-route-main').scrollHeight,height:document.querySelector('.server-route-main').clientHeight,document:document.querySelector('.server-route-document').scrollHeight,documentHeight:document.querySelector('.server-route-document').clientHeight,compact:document.querySelector('.review-shell').classList.contains('is-compact')})",
      );
      assert(size.doc <= height + 1, JSON.stringify(size));
      assert(size.main <= size.height + 1, JSON.stringify(size));
      assert(size.document <= size.documentHeight + 1, JSON.stringify(size));
      if (size.compact) {
        for (const pane of ["navigation", "inspector", "document"]) {
          await evaluate(
            `document.querySelector('[data-review-pane=${pane}]').click()`,
          );
          assert.equal(
            await evaluate(
              'document.querySelector(".review-shell").dataset.reviewPane',
            ),
            pane,
          );
        }
      }
      assert.equal(
        await evaluate(
          "document.querySelectorAll('.artifact-panel.is-active').length",
        ),
        1,
      );
      await evaluate(
        "document.querySelector('.artifact-panel.is-active .rich-pane').insertAdjacentHTML('beforeend','<p style=height:2000px>Document end</p>');document.querySelector('.artifact-panel.is-active .rich-pane').scrollTop=10000",
      );
      assert.equal(
        await evaluate(
          "document.querySelector('.server-route-main').scrollTop",
        ),
        0,
      );
      record.checks.push({
        check:
          "scaffold fixed studio, reachable compact panes, single document and inner-scroll",
        width,
        height,
        ...size,
      });
    }
  }
  assert.equal(record.exceptions.length, 0, JSON.stringify(record.exceptions));
  record.status = "passed";
  if (output) writeFileSync(output, JSON.stringify(record, null, 2));
  console.log(JSON.stringify(record, null, 2));
} finally {
  if (ws) ws.close();
  chrome.kill("SIGTERM");
  server.close();
  await new Promise((resolve) => chrome.once("exit", resolve));
  rmSync(profile, { recursive: true, force: true });
}
