// Actual Chromium DOM/pointer/focus regression; builtins and synthetic data only.
import { readFile, mkdir } from "node:fs/promises";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import assert from "node:assert/strict";
const [input, executable, profile] = process.argv.slice(2),
  checks = [];
const check = (name, value, detail) => {
  checks.push({ name, ok: Boolean(value), detail });
  if (
    !value &&
    process.env.VIBECRAFTED_USAGE_DOM_RED_ONLY === "1" &&
    [
      "background_header_retained",
      "unchanged_chart_nodes_and_geometry",
      "unchanged_17223_rows_not_recreated",
      "hover_focus_selection_preserved",
      "quota_disclosure_and_focus_retained",
    ].includes(name)
  )
    return;
  assert.ok(value, name);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (fn, label, ms = 15000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const value = await fn();
    if (value) return value;
    await sleep(25);
  }
  throw Error("Timeout: " + label);
};
const generated = Date.parse("2026-10-30T15:00:00Z");
const rows = Array.from({ length: 17223 }, (_, i) => ({
  run_id: "fixture-" + String(i).padStart(5, "0"),
  origin: i < 2000 ? "vc" : "local",
  provider: "openai",
  agent: "codex",
  model: "fixture-model-" + (i % 3),
  status: i < 2000 ? "completed" : "observed",
  exit_code: i < 2000 ? 0 : null,
  recorded_at: new Date(generated - (i % 30) * 86400000).toISOString(),
  tokens: {
    schema: "vibecrafted.usage.v1",
    unit: "tokens",
    source: "synthetic_native",
    events: 1,
    tokens_input: 100,
    tokens_cached_input: 3,
    tokens_cache_write: {
      value: "unknown",
      reason: "not emitted by synthetic provider",
    },
    tokens_output: 12,
    tokens_total: 115,
    counting_version: "native-fixture-v1",
    model_usage: {
      input_semantics:
        "Synthetic recorded counters; never private conversation data. ".repeat(
          2,
        ),
    },
  },
  cost: {
    amount: 0.01,
    currency: "USD",
    source: "fixture_native",
    unit: "USD",
  },
  provider_session_id: {
    value: "unknown",
    reason: "synthetic browser regression",
  },
  telemetry_source: "synthetic_native",
  failure: null,
  failure_kind: null,
}));
rows.at(-1).tokens.tokens_total = {
  value: "unknown",
  reason: "native source unavailable",
};
rows.at(-1).cost.amount = { value: "unknown", reason: "price unavailable" };
const state = {
  phase: "full",
  pending: false,
  extra: 0,
  day: 0,
  hold: false,
  quota: "fresh",
  ratio: 0.39,
};
const full = () =>
  rows.map((row, i) =>
    i === 0
      ? {
          ...row,
          tokens: { ...row.tokens, tokens_total: 115 + state.extra },
          cost: { ...row.cost, amount: 0.01 + state.extra / 100 },
        }
      : row,
  );
const replacement = () => [
  ...full().filter((r) => r.run_id !== "fixture-02000"),
  ...Array.from({ length: 4 }, (_, i) => ({
    ...rows[i],
    run_id: i ? "new-fixture-" + i : "new-vc-twin",
    origin: "vc",
  })),
];
const requests = [],
  held = [];
let counter = 0;
const payload = (q) => {
  let data =
    state.phase === "loading"
      ? [
          ...full().slice(0, 2000),
          { ...rows[2000], run_id: "new-vc-twin", origin: "vc" },
        ]
      : ["reconciled", "deleted"].includes(state.phase)
        ? replacement()
        : full();
  if (state.phase === "deleted")
    data = data.filter((r) => r.run_id !== "fixture-00010");
  const window = q.get("window") || "24h";
  if (window !== "all")
    data = data.filter(
      (r) =>
        Date.parse(r.recorded_at) >=
        generated -
          (window === "7d" ? 6 : window === "30d" ? 29 : 0) * 86400000,
    );
  if (q.get("model")) data = data.filter((r) => r.model === q.get("model"));
  if (q.get("provider") && q.get("provider") !== "openai") data = [];
  const totals = {
    runs: data.length,
    runs_failed: 0,
    tokens_total_known: data.reduce(
      (s, r) =>
        s +
        (typeof r.tokens.tokens_total === "number" ? r.tokens.tokens_total : 0),
      0,
    ),
    runs_tokens_unknown: data.filter(
      (r) => typeof r.tokens.tokens_total !== "number",
    ).length,
    runs_cost_unknown: data.filter((r) => typeof r.cost.amount !== "number")
      .length,
    cost_by_unit: {
      USD: data.reduce(
        (s, r) => s + (typeof r.cost.amount === "number" ? r.cost.amount : 0),
        0,
      ),
    },
  };
  const dim = (key) =>
    [...new Set(data.map((r) => r[key]))].map((name) => ({
      ...totals,
      name,
      runs: data.filter((r) => r[key] === name).length,
    }));
  return {
    schema: "vibecrafted.usage-report.v1",
    generated_at: new Date(
      generated + state.day * 86400000 + counter++ * 1000,
    ).toISOString(),
    filter: { since: window },
    runs: data,
    totals,
    dimensions: {
      providers: dim("provider"),
      agents: dim("agent"),
      models: dim("model"),
    },
    local_sources: [
      {
        agent: "codex",
        source: state.phase === "loading" ? "loading" : "scan_cache",
        counted:
          state.phase === "loading"
            ? 0
            : data.filter((r) => r.origin === "local").length,
        refreshing: state.phase === "loading",
        stale: state.phase === "loading",
      },
    ],
    recovery: {
      pending_runs: state.pending ? 4 : 0,
      refreshing: state.pending,
      unavailable_runs: 151,
      stale_runs: 0,
      stale_fields: {},
      failure_reasons: { timeout: 2 },
    },
  };
};
const quota = () => ({
  generated_at: new Date(generated + counter++ * 1000).toISOString(),
  agents: [
    {
      id: "codex",
      name: "Codex",
      state: state.quota === "fresh" ? "ok" : "stale",
      status: "ok",
      stale: state.quota !== "fresh",
      headline: Math.round((1 - state.ratio) * 100) + "% remaining",
      detail: "Synthetic native weekly observation",
      age_s: counter,
      scope: { pool: "openai", limit_id: "codex", plan_type: "fixture" },
      source: "synthetic_native",
      tokens: 115,
      bars: [
        {
          id: "weekly",
          label: "7d",
          ratio: state.ratio,
          text: Math.round(state.ratio * 100) + "% used",
          resets_at: 1793776199,
          stale: state.quota !== "fresh",
        },
      ],
    },
  ],
});
let html = await readFile(input, "utf8");
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(
  (m) => m[1],
);
let usageScript = scripts.find((s) =>
    s.includes("document.querySelector('[data-usage-dashboard]')"),
  ),
  quotaScript = scripts.find((s) =>
    s.includes("document.querySelector('[data-quota-board]')"),
  );
assert.ok(usageScript && quotaScript, "both canonical SSR Usage scripts");
if (process.env.VIBECRAFTED_USAGE_BEFORE) {
  usageScript = await readFile(
    process.env.VIBECRAFTED_USAGE_BEFORE + "/usage_dashboard_script-before.js",
    "utf8",
  );
  quotaScript = await readFile(
    process.env.VIBECRAFTED_USAGE_BEFORE + "/quota_dashboard_script-before.js",
    "utf8",
  );
}
html = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, "");
const css = await readFile(process.env.VIBECRAFTED_TEST_CSS, "utf8");
html =
  '<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><style>' +
  css +
  "</style></head><body>" +
  html +
  '<script>document.getElementById("usage-window").value="all";</script><script>' +
  quotaScript +
  "</script><script>" +
  usageScript +
  "</script></body></html>";
const server = createServer((req, res) => {
  const url = new URL(req.url, "http://fixture.test");
  if (url.pathname === "/usage") {
    res.setHeader("content-type", "text/html");
    res.end(html);
    return;
  }
  if (url.pathname === "/api/usage/quota") {
    requests.push({ quota: true });
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(quota()));
    return;
  }
  if (url.pathname === "/api/usage") {
    const body = JSON.stringify(payload(url.searchParams));
    requests.push({ query: url.search, bytes: Buffer.byteLength(body) });
    const answer = () => {
      res.setHeader("content-type", "application/json");
      res.end(body);
    };
    if (state.hold) held.push({ answer, response: res, query: url.search });
    else answer();
    return;
  }
  res.writeHead(404);
  res.end();
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
await mkdir(profile, { recursive: true, mode: 0o700 });
const chrome = spawn(
  executable,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-breakpad",
    "--window-size=1400,1000",
    "--remote-debugging-port=0",
    "--user-data-dir=" + profile,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
let browserError = "",
  socket,
  result;
chrome.stderr.on(
  "data",
  (d) => (browserError = (browserError + d).slice(-4000)),
);
try {
  const port = await until(async () => {
    try {
      return (await readFile(profile + "/DevToolsActivePort", "utf8")).split(
        "\n",
      )[0];
    } catch {
      if (chrome.exitCode !== null)
        throw Error("Chromium exited: " + browserError);
      return false;
    }
  }, "browser port");
  const targets = await (
    await fetch("http://127.0.0.1:" + port + "/json/list")
  ).json();
  socket = new WebSocket(
    targets.find((t) => t.type === "page").webSocketDebuggerUrl,
  );
  await once(socket, "open");
  let sequence = 0;
  const calls = new Map();
  socket.addEventListener("message", ({ data }) => {
    const m = JSON.parse(data);
    if (!m.id) return;
    const p = calls.get(m.id);
    if (!p) return;
    calls.delete(m.id);
    if (m.error) p.reject(Error(JSON.stringify(m.error)));
    else p.resolve(m.result);
  });
  const cdp = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++sequence;
      calls.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  const evaluate = async (expression) => {
    const a = await cdp("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (a.exceptionDetails)
      throw Error(a.exceptionDetails.text + ": " + a.result.description);
    return a.result.value;
  };
  await cdp("Page.enable");
  await cdp("Page.addScriptToEvaluateOnNewDocument", {
    source: `window.__usageDom={hidden:false,timers:new Map(),intervals:[],rowsCreated:0};Object.defineProperty(document,'hidden',{configurable:true,get:()=>__usageDom.hidden});const timeout=window.setTimeout.bind(window),clear=window.clearTimeout.bind(window),interval=window.setInterval.bind(window);window.setTimeout=(fn,delay,...args)=>{let id;id=timeout(()=>{__usageDom.timers.delete(id);fn(...args)},delay);__usageDom.timers.set(id,{fn,delay,id});return id};window.clearTimeout=id=>{__usageDom.timers.delete(id);clear(id)};window.setInterval=(fn,delay,...args)=>{__usageDom.intervals.push({fn,delay});return interval(fn,delay,...args)};const create=document.createElement.bind(document);document.createElement=(tag,...args)=>{if(tag.toLowerCase()==='tr')__usageDom.rowsCreated++;return create(tag,...args)};`,
  });
  await cdp("Page.navigate", {
    url: "http://127.0.0.1:" + server.address().port + "/usage?window=all",
  });
  await until(
    () =>
      evaluate(
        "document.getElementById('usage-run-count')?.textContent==='17223'",
      ),
    "first17223rows",
  );
  const fixtureBytes = requests.find((r) => r.bytes).bytes;
  check("realistic_payload_scale", fixtureBytes >= 12_000_000, fixtureBytes);
  check(
    "first_full_rows",
    await evaluate(
      "document.querySelectorAll('#usage-runs-body tr').length===17223",
    ),
  );
  await evaluate(
    `window.refs={heat:document.querySelector('#usage-chart-heat-plot svg'),cost:document.querySelector('#usage-chart-cost-plot svg'),line:document.querySelector('#usage-chart-cost-plot polyline'),row:document.querySelectorAll('#usage-runs-body tr')[1],picker:document.getElementById('usage-provider'),option:document.getElementById('usage-provider').options[1],source:document.querySelector('#quota-agents details'),summary:document.querySelector('#quota-agents summary'),count:__usageDom.rowsCreated};refs.rect=refs.heat.querySelector('rect[fill-opacity]');refs.source.open=true;refs.row.dispatchEvent(new MouseEvent('click',{bubbles:true}));refs.rect.scrollIntoView({block:'center'});refs.picker.focus({preventScroll:true});window.beforeStatus=document.getElementById('usage-status').textContent;window.beforeGenerated=document.getElementById('usage-generated').textContent;window.chartMutations=0;new MutationObserver(rows=>chartMutations+=rows.length).observe(refs.heat,{subtree:true,childList:true,attributes:true});`,
  );
  const point = await evaluate(
    "(()=>{const b=refs.rect.getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2}})()",
  );
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", ...point });
  await until(
    () => evaluate("refs.rect.matches(':hover')"),
    "native pointer hover",
    1000,
  );
  check(
    "native_pointer_hover_established",
    await evaluate("refs.rect.matches(':hover')"),
    await evaluate(
      "(()=>{const b=refs.rect.getBoundingClientRect();return{connected:refs.rect.isConnected,scroll:scrollY,rect:{x:b.x,y:b.y,w:b.width,h:b.height},hit:document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)?.tagName}})()",
    ),
  );
  state.pending = true;
  state.hold = true;
  await evaluate(
    "__usageDom.intervals.find(i=>i.delay===15000&&!String(i.fn).includes('/api/usage/quota')).fn()",
  );
  await until(() => held.length > 0, "recovery request");
  check(
    "background_header_retained",
    await evaluate(
      "document.getElementById('usage-status').textContent===beforeStatus",
    ),
  );
  state.hold = false;
  held.splice(0).forEach((r) => r.answer());
  await until(
    () =>
      evaluate(
        "document.getElementById('usage-generated').textContent!==beforeGenerated",
      ),
    "background response rendered",
  );
  check(
    "unchanged_chart_nodes_and_geometry",
    await evaluate(
      "document.querySelector('#usage-chart-heat-plot svg')===refs.heat&&document.querySelector('#usage-chart-cost-plot svg')===refs.cost&&chartMutations===0",
    ),
  );
  check(
    "unchanged_17223_rows_not_recreated",
    await evaluate(
      "__usageDom.rowsCreated===refs.count&&document.querySelectorAll('#usage-runs-body tr')[1]===refs.row",
    ),
  );
  check(
    "hover_focus_selection_preserved",
    await evaluate(
      "refs.rect.matches(':hover')&&document.activeElement===refs.picker&&refs.row.classList.contains('is-selected')&&refs.picker.options[1]===refs.option",
    ),
  );
  const quotaTick = async () => {
    await evaluate(
      "__usageDom.intervals.find(i=>i.delay===15000&&String(i.fn).includes('/api/usage/quota')).fn()",
    );
    await sleep(80);
  };
  await evaluate("refs.summary.focus({preventScroll:true})");
  await quotaTick();
  check(
    "quota_disclosure_and_focus_retained",
    await evaluate(
      "document.querySelector('#quota-agents details')===refs.source&&refs.source.open&&document.activeElement===refs.summary",
    ),
  );
  if (process.env.VIBECRAFTED_USAGE_DOM_RED_ONLY === "1")
    throw Error(
      checks.some((check) => !check.ok)
        ? "Baseline refresh loses retained UI state"
        : "Baseline unexpectedly retained all refresh state",
    );
  const tick = () =>
    evaluate(
      "(async()=>{const t=[...__usageDom.timers.values()].find(i=>[1500,5000,15000].includes(i.delay));if(!t)throw Error('no recovery timer');clearTimeout(t.id);await t.fn()})()",
    );
  const timerDelay = () =>
    evaluate(
      "[...__usageDom.timers.values()].find(i=>[1500,5000,15000].includes(i.delay))?.delay",
    );
  const delays = [await timerDelay()];
  for (let i = 0; i < 8; i++) {
    await tick();
    delays.push(await timerDelay());
  }
  check(
    "recovery_polling_bounded",
    delays.includes(5000) && delays.includes(15000),
    delays,
  );
  await evaluate(
    "refs.picker.value='anthropic';refs.picker.focus({preventScroll:true})",
  );
  await tick();
  check(
    "background_preserves_draft_filter",
    requests.filter((r) => r.query).at(-1).query === "?window=all" &&
      (await evaluate(
        "refs.picker.value==='anthropic'&&document.activeElement===refs.picker",
      )),
  );
  await evaluate(
    "refs.picker.value='';refs.picker.blur();window.oldPoints=refs.line.getAttribute('points');window.hoverNode=refs.rect",
  );
  state.extra = 50;
  await tick();
  check(
    "changed_measurement_updates_without_teardown",
    await evaluate(
      "document.querySelector('#usage-chart-cost-plot svg')===refs.cost&&refs.line.getAttribute('points')!==oldPoints&&document.querySelector('#usage-chart-heat-plot svg')===refs.heat&&refs.rect===hoverNode&&document.querySelector('#usage-runs-body tr td:nth-child(4)').textContent==='165'",
    ),
  );
  await evaluate("document.documentElement.dataset.theme='light'");
  await sleep(30);
  check(
    "light_palette_updates_retained_heat_cells",
    await evaluate(
      "refs.rect.isConnected&&refs.rect.getAttribute('fill')==='#2f6b32'",
    ),
  );
  await evaluate("document.documentElement.dataset.theme='dark'");
  await sleep(30);
  check(
    "dark_palette_updates_retained_heat_cells",
    await evaluate("refs.rect.getAttribute('fill')==='#90a959'"),
  );
  state.phase = "loading";
  await tick();
  check(
    "unknowns_and_unavailable_remain_explicit",
    await evaluate(
      "document.getElementById('usage-total-token-unknown').textContent==='1'&&document.getElementById('usage-total-cost-unknown').textContent==='1'&&document.getElementById('usage-status').textContent.includes('151 transcript source(s) unavailable')&&document.querySelectorAll('#usage-runs-body tr').item(17222).classList.contains('is-unknown')",
    ),
  );
  check(
    "temporary_partial_keeps_coherent_snapshot",
    await evaluate(
      "document.getElementById('usage-run-count').textContent==='17223'&&document.querySelector('[data-usage-dashboard]').dataset.measurements==='previous'&&!document.querySelector('[data-run-id=\"new-vc-twin\"]')&&document.getElementById('usage-status').textContent.includes('Previous available')&&document.getElementById('usage-generated').textContent.includes('as of')",
    ),
  );
  state.phase = "reconciled";
  await tick();
  check(
    "replacement_accepts_new_vc_twin_without_double_counting",
    await evaluate(
      "document.getElementById('usage-run-count').textContent==='17226'&&document.querySelector('[data-run-id=\"new-vc-twin\"]')&&!document.querySelector('[data-row-key=\"fixture-02000\"]')",
    ),
  );
  state.phase = "deleted";
  await tick();
  check(
    "authoritative_deletion_is_not_hidden",
    await evaluate(
      "document.getElementById('usage-run-count').textContent==='17225'&&!document.querySelector('[data-run-id=\"fixture-00010\"]')",
    ),
  );
  state.phase = "loading";
  await evaluate(
    "document.getElementById('usage-window').value='7d';document.getElementById('usage-filter-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))",
  );
  await until(
    () =>
      evaluate(
        "location.search==='?window=7d'&&document.querySelector('[data-usage-dashboard]').dataset.measurements==='partial'",
      ),
    "new filterpartial",
  );
  check(
    "changed_filter_does_not_reuse_other_scope",
    await evaluate(
      "Number(document.getElementById('usage-run-count').textContent)<2001&&!document.getElementById('usage-status').textContent.includes('Previous available')",
    ),
  );
  state.phase = "full";
  await tick();
  check(
    "filtered_full_result_matches_scope",
    await evaluate(
      "Number(document.getElementById('usage-run-count').textContent)<17223&&document.getElementById('usage-chart-heat-title').textContent.includes('7d')",
    ),
  );
  const days = await evaluate(
    "document.querySelectorAll('#usage-chart-heat-plot rect').length",
  );
  state.day++;
  await tick();
  check(
    "window_day_boundary_recomputed",
    days === 7 &&
      (await evaluate(
        "document.querySelectorAll('#usage-chart-heat-plot rect').length===7&&[...document.querySelectorAll('#usage-chart-heat-plot rect')].some(r=>r.getAttribute('title').startsWith('2026-10-31'))",
      )),
  );
  state.hold = true;
  await evaluate(
    "document.getElementById('usage-window').value='24h';document.getElementById('usage-filter-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))",
  );
  await until(
    () => held.some((r) => r.query === "?window=24h"),
    "held old filter",
  );
  await evaluate(
    "document.getElementById('usage-window').value='30d';document.getElementById('usage-filter-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))",
  );
  await until(
    () => held.some((r) => r.query === "?window=30d"),
    "held latest filter",
  );
  state.hold = false;
  held.find((r) => r.query === "?window=30d").answer();
  await until(
    () => evaluate("location.search==='?window=30d'"),
    "latest filter accepted",
  );
  held.filter((r) => r.query === "?window=24h").forEach((r) => r.answer());
  held.length = 0;
  await sleep(80);
  check(
    "late_filter_response_cannot_replace_latest_scope",
    await evaluate(
      "location.search==='?window=30d'&&document.getElementById('usage-chart-heat-title').textContent.includes('30d')&&document.getElementById('usage-run-count').textContent==='17223'",
    ),
  );
  state.ratio = 0.6;
  state.quota = "stale";
  await evaluate(
    "refs.source.open=true;refs.summary.focus({preventScroll:true})",
  );
  await quotaTick();
  check(
    "quota_move_retains_disclosure_focus_and_semantics",
    await evaluate(
      "refs.source.isConnected&&document.getElementById('quota-unmeasured').contains(refs.source)&&refs.source.open&&document.activeElement===refs.summary&&refs.source.closest('article').querySelector('.quota-headline').hidden&&document.getElementById('quota-unmeasured').closest('details').open",
    ),
  );
  await evaluate(
    "document.getElementById('quota-unmeasured').closest('details').open=false",
  );
  await quotaTick();
  check(
    "manual_disclosure_close_survives_poll",
    await evaluate(
      "!document.getElementById('quota-unmeasured').closest('details').open",
    ),
  );
  state.pending = false;
  await tick();
  check(
    "pending_converges_without_stopping_normal_updates",
    (await timerDelay()) === undefined,
  );
  const count = requests.length;
  await evaluate(
    "__usageDom.hidden=true;document.dispatchEvent(new Event('visibilitychange'));__usageDom.intervals.forEach(i=>i.fn())",
  );
  await sleep(80);
  check("hidden_page_suspends_background_requests", requests.length === count);
  await evaluate(
    "__usageDom.hidden=false;document.dispatchEvent(new Event('visibilitychange'))",
  );
  await until(() => requests.length > count, "visible resume");
  check("visible_page_resumes_updates", requests.length > count);
  result = {
    ok: true,
    fixture_rows: 17223,
    fixture_bytes: fixtureBytes,
    checks,
    usage_requests: requests.filter((r) => r.query).length,
    quota_requests: requests.filter((r) => r.quota).length,
  };
} catch (error) {
  result = {
    ok: false,
    fixture_rows: 17223,
    fixture_bytes: requests.find((r) => r.bytes)?.bytes || 0,
    checks,
    error: error.message,
  };
  process.exitCode = 1;
} finally {
  if (socket) socket.close();
  chrome.kill("SIGTERM");
  await Promise.race([once(chrome, "exit"), sleep(3000)]);
  if (chrome.exitCode === null) {
    chrome.kill("SIGKILL");
    await once(chrome, "exit");
  }
  server.closeAllConnections();
  server.close();
  console.log(JSON.stringify(result, null, 2));
}
