(() => {
  const root = document.querySelector("[data-usage-dashboard]");
  if (!root) return;
  const byId = (id) => document.getElementById(id);
  const form = byId("usage-filter-form");
  const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 });
  const known = (value) =>
    typeof value === "number" && Number.isFinite(value) && value >= 0;
  const knownTokens = (value) => Number.isSafeInteger(value) && value >= 0;
  const text = (value) =>
    known(value)
      ? fmt.format(value)
      : typeof value === "string" && value
        ? value
        : "unknown";
  const costKey = (run) =>
    JSON.stringify([
      run.cost?.source || "unknown",
      run.cost?.unit || run.cost?.currency || "unknown",
    ]);
  const costLabel = (key) => {
    const [source, unit] = JSON.parse(key);
    return `${source.startsWith("estimated:") ? "API estimate" : source === "provider_reported" ? "Provider report" : "Unclassified cost"} · ${unit} · ${source}`;
  };
  const stamp = (run) => Date.parse(run.recorded_at);
  const set = (id, value) => {
    if (byId(id)) byId(id).textContent = value;
  };
  const fieldKeys = [
    "window",
    "provider",
    "agent",
    "model",
    "project",
    "task",
    "parent",
    "outcome",
    "from",
    "to",
  ];
  const initial = new URLSearchParams(location.search);
  for (const key of fieldKeys)
    if (initial.has(key)) byId("usage-" + key).value = initial.get(key);
  let report = null,
    rows = [],
    filtered = [],
    evidence = [],
    page = Math.max(0, Number.parseInt(initial.get("page"), 10) || 0),
    selected = initial.get("run") || "";
  let view = initial.get("view") || "time",
    dimension = initial.get("dimension") || "providers",
    doc = initial.get("doc") || "usage";
  let selection = null,
    series = initial.get("series") || "",
    documentSequence = 0,
    loadSequence = 0;
  let growthIds = new Set();
  try {
    selection = JSON.parse(initial.get("selection") || "null");
  } catch (_) {
    /* malformed optional selection */
  }
  const save = () => {
    const query = new URLSearchParams(new FormData(form));
    for (const [key, value] of [...query]) if (!value) query.delete(key);
    query.set("page", String(page));
    query.set("view", view);
    query.set("dimension", dimension);
    query.set("doc", doc);
    if (selected) query.set("run", selected);
    if (selection) query.set("selection", JSON.stringify(selection));
    if (series) query.set("series", series);
    history.replaceState(null, "", "/usage?" + query);
  };
  const aggregate = (records) => {
    const result = {
      runs: records.length,
      tokens: 0,
      tokenUnknown: 0,
      costUnknown: 0,
      failed: 0,
      duplicates: 0,
      duration: 0,
      durationUnknown: 0,
      settlements: 0,
      costs: new Map(),
      sources: new Set(),
    };
    for (const run of records) {
      if (
        (run.exit_code !== null &&
          run.exit_code !== undefined &&
          run.exit_code !== 0) ||
        run.failure_kind
      )
        result.failed++;
      if (run.settlement_verdict) result.settlements++;
      result.sources.add(run.tokens?.source || "unknown");
      if (run.duplicate_of) {
        result.duplicates++;
        continue;
      }
      if (known(run.duration_s)) result.duration += run.duration_s;
      else result.durationUnknown++;
      if (knownTokens(run.tokens?.tokens_total))
        result.tokens += run.tokens.tokens_total;
      else result.tokenUnknown++;
      if (known(run.cost?.amount))
        result.costs.set(
          costKey(run),
          (result.costs.get(costKey(run)) || 0) + run.cost.amount,
        );
      else result.costUnknown++;
    }
    return result;
  };
  const costsText = (totals) =>
    [...totals.costs]
      .map(([key, value]) => `${fmt.format(value)} ${costLabel(key)}`)
      .join(" | ") || (totals.runs ? "unknown" : "no records");
  const period = () => {
    const mode = byId("usage-window").value;
    const end = Date.parse(report?.generated_at) || Date.now();
    const hours = { "24h": 24, "7d": 168, "30d": 720 }[mode];
    if (hours) return [end - hours * 3600000, end];
    if (mode === "custom")
      return [
        Date.parse(byId("usage-from").value + "Z"),
        Date.parse(byId("usage-to").value + "Z"),
      ];
    return [-Infinity, Infinity];
  };
  const matches = (run) => {
    for (const [field, key] of [
      ["provider", "provider"],
      ["agent", "agent"],
      ["model", "model"],
      ["project", "root"],
      ["task", "task"],
      ["parent", "parent_run_id"],
    ]) {
      const wanted = byId("usage-" + field)
        .value.trim()
        .toLowerCase();
      if (wanted && text(run[key]).toLowerCase() !== wanted) return false;
    }
    const outcome = byId("usage-outcome").value;
    return (
      !outcome ||
      (outcome === "failed" &&
        ((run.exit_code != null && run.exit_code !== 0) || run.failure_kind)) ||
      (outcome === "completed" && run.status === "completed") ||
      (outcome === "settled" && !!run.settlement_verdict) ||
      (outcome === "unknown" && !run.settlement_verdict)
    );
  };
  const groupField = {
    providers: "provider",
    agents: "agent",
    models: "model",
    parents: "parent_run_id",
    tasks: "task",
  };
  const selectionMatches = (run) => {
    if (!selection) return true;
    if (selection.kind === "range")
      return (
        stamp(run) >= selection.from &&
        stamp(run) < selection.to &&
        (!selection.series ||
          (costKey(run) === selection.series && known(run.cost?.amount)))
      );
    if (selection.kind === "group")
      return text(run[groupField[selection.dimension]]) === selection.value;
    if (selection.kind === "signal")
      return selection.rule === "growth"
        ? growthIds.has(run.run_id)
        : (run.signals || []).some((signal) =>
            signal.startsWith(selection.rule + ":"),
          );
    return true;
  };
  const describe = (records, label) => {
    const totals = aggregate(records);
    const bucketTotals = [
      "tokens_input",
      "tokens_cached_input",
      "tokens_cache_write",
      "tokens_output",
      "tokens_reasoning",
    ].map((key) => {
      const measured = records.filter(
        (run) => !run.duplicate_of && knownTokens(run.tokens?.[key]),
      );
      return `${key}: ${fmt.format(measured.reduce((sum, run) => sum + run.tokens[key], 0))} (${measured.length}/${records.length} measured)`;
    });
    return `${label} · ${totals.runs} records · ${fmt.format(totals.tokens)} known tokens (${totals.tokenUnknown} unknown, ${totals.duplicates} identical replays excluded) · ${costsText(totals)} · ${bucketTotals.join(" · ")} · sources: ${[...totals.sources].join(", ")} · refreshed ${report?.generated_at || "unknown"}. Cache/reasoning fields are not added to totals; inspect each provider's semantics.`;
  };
  const showInfo = (records, label) =>
    set("usage-hover", describe(records, label));
  const choose = (next) => {
    selection = next;
    page = 0;
    selected = "";
    byId("overview-inspector").hidden = true;
    save();
    render();
  };
  const bind = (node, records, label, next) => {
    node.setAttribute("tabindex", "0");
    node.setAttribute("role", "button");
    node.setAttribute("aria-label", describe(records, label));
    node.addEventListener("mouseenter", () => showInfo(records, label));
    node.addEventListener("focus", () => showInfo(records, label));
    const activate = (event) => {
      if (
        event.shiftKey &&
        selection?.kind === "range" &&
        next.kind === "range"
      )
        next = {
          ...next,
          from: Math.min(selection.from, next.from),
          to: Math.max(selection.to, next.to),
        };
      choose(next);
      showInfo(records, label);
    };
    node.addEventListener("click", activate);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate(event);
      }
    });
  };
  const showView = (name) => {
    view = ["time", "dimensions", "attention", "quota"].includes(name)
      ? name
      : "time";
    root
      .querySelectorAll("[data-usage-panel]")
      .forEach((panel) => (panel.hidden = panel.dataset.usagePanel !== view));
    root
      .querySelectorAll("[data-usage-view]")
      .forEach((button) =>
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.usageView === view),
        ),
      );
    save();
  };
  const showDimension = (name) => {
    dimension = groupField[name] ? name : "providers";
    for (const key of Object.keys(groupField))
      byId("usage-" + key).hidden = key !== dimension;
    root
      .querySelectorAll("[data-usage-dim]")
      .forEach((button) =>
        button.setAttribute(
          "aria-selected",
          String(button.dataset.usageDim === dimension),
        ),
      );
    save();
  };
  const showDoc = (name) => {
    doc = ["usage", "transcript", "report", "structure"].includes(name)
      ? name
      : "usage";
    root
      .querySelectorAll("[data-usage-doc-panel]")
      .forEach((panel) => (panel.hidden = panel.dataset.usageDocPanel !== doc));
    root
      .querySelectorAll("[data-usage-doc]")
      .forEach((button) =>
        button.classList.toggle("is-active", button.dataset.usageDoc === doc),
      );
    save();
  };
  const selectRun = async (run) => {
    const sequence = ++documentSequence;
    selected = run.run_id;
    save();
    const pane = byId("overview-inspector");
    pane.hidden = false;
    root
      .querySelectorAll("tr[data-run-id]")
      .forEach((row) =>
        row.classList.toggle("is-selected", row.dataset.runId === selected),
      );
    pane.querySelector("[data-inspector-id]").textContent = selected;
    pane.querySelector("[data-inspector-meta]").textContent =
      `${text(run.agent)} · ${text(run.model)} · ${run.status}`;
    pane.querySelector("[data-inspector-root]").textContent =
      run.root || "Project root unknown";
    const detail = "/run/" + encodeURIComponent(selected);
    byId("usage-run-detail").href = detail;
    pane.querySelector("[data-inspector-open]").href = detail;
    const tokens = run.tokens || {};
    const semantics =
      tokens.input_semantics ||
      (tokens.counting_version === 1
        ? "legacy provider stream; categories may overlap"
        : "unknown");
    byId("usage-measurement").textContent = [
      `Run: ${selected}`,
      `Task: ${run.task || "unknown"}`,
      `Parent: ${run.parent_run_id || "unknown / unrecorded"}`,
      `Provider session: ${text(run.provider_session_id)}`,
      `Window: ${run.started_at || "start unknown"} → ${run.recorded_at} (assigned to completion/update, not event timestamps)`,
      `Recorded duration: ${text(run.duration_s)} seconds`,
      `Status: ${run.status}; exit ${text(run.exit_code)} is process evidence, not accepted delivery`,
      `Settlement: ${run.settlement_verdict || "unknown"} · ${run.settlement_reason || "no recorded reason"}`,
      `Accepted delivery / revisions / quality: unknown; use existing run proof and admission receipts`,
      `Token unit: ${tokens.unit || "unknown"}; source: ${tokens.source || "unknown"}; events: ${tokens.events_recorded === false ? "unknown" : (tokens.events ?? "unknown")}; counting version: ${tokens.counting_version ?? "unknown"}`,
      `Input semantics: ${semantics}`,
      ...[
        "tokens_input",
        "tokens_cached_input",
        "tokens_cache_write",
        "tokens_output",
        "tokens_reasoning",
        "tokens_total",
      ].map(
        (key) =>
          `${key}: ${text(tokens[key])}${tokens[key]?.reason ? " — " + tokens[key].reason : ""}`,
      ),
      `Model components (disjoint only where the owner supplies them): ${JSON.stringify(tokens.model_usage || "unknown", null, 2)}`,
      `Cost: ${text(run.cost?.amount)} ${run.cost?.unit || run.cost?.currency || "unit unknown"}; ${run.cost?.source || "source unknown"}`,
      `Cost reason: ${run.cost?.amount?.reason || "no additional reason recorded"}; unpriced models: ${(run.unpricedModels || []).join(", ") || "none listed"}`,
      `Actual billing: unknown; API estimate is not a subscription invoice`,
      `Projection source: ${run.telemetry_source}; refreshed: ${report.generated_at}`,
      `Counted: ${run.duplicate_of ? "excluded identical replay of " + run.duplicate_of : "yes; review any attribution signals"}`,
      ...(run.signals || []),
      `Recorded report: ${run.report_path || "unknown"}`,
    ].join("\n");
    showDoc(doc);
    for (const kind of ["transcript", "report"]) {
      const target = pane.querySelector(
        kind === "report" ? "[data-inspector-report]" : "[data-inspector-tail]",
      );
      target.textContent = "Loading " + kind + "…";
      try {
        const response = await fetch(
          "/api/control/runs/" + encodeURIComponent(selected) + "/" + kind,
          { cache: "no-store" },
        );
        const payload = await response.json();
        if (sequence !== documentSequence) return;
        target.textContent =
          response.ok && payload.available
            ? payload.body +
              (payload.truncated
                ? "\n[Bounded preview; document truncated]"
                : "")
            : kind + " unavailable in the canonical artifact scope.";
      } catch (_) {
        if (sequence === documentSequence)
          target.textContent = kind + " unavailable.";
      }
    }
  };
  const renderRows = () => {
    const body = byId("usage-runs-body");
    body.replaceChildren();
    page = Math.max(0, Math.min(page, Math.ceil(evidence.length / 100) - 1));
    for (const run of evidence.slice(page * 100, (page + 1) * 100)) {
      const row = document.createElement("tr");
      row.dataset.runId = run.run_id;
      row.tabIndex = 0;
      if (run.signals?.length || run.failure_kind)
        row.classList.add("is-attention");
      const link = document.createElement("a");
      link.href = "/run/" + encodeURIComponent(run.run_id);
      link.textContent = run.run_id;
      const id = document.createElement("td");
      id.append(link);
      const timestamp = document.createElement("small");
      timestamp.textContent = run.recorded_at;
      id.append(timestamp);
      row.append(id);
      const result = `${run.status} · settlement ${run.settlement_verdict || "unknown"} · ${known(run.duration_s) ? fmt.format(run.duration_s) + "s" : "time unknown"}`;
      for (const value of [
        `${text(run.provider)} / ${text(run.agent)}`,
        text(run.model),
        `${text(run.tokens?.tokens_total)}${run.duplicate_of ? " (replay excluded)" : ""}`,
        `${text(run.cost?.amount)} ${run.cost?.unit || run.cost?.currency || ""} · ${run.cost?.source || "unknown"}`,
        result,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      row.addEventListener("click", (event) => {
        if (event.target.closest("a")) {
          save();
          return;
        }
        event.stopPropagation();
        selectRun(run);
      });
      row.addEventListener("keydown", (event) => {
        if (
          event.target === row &&
          (event.key === "Enter" || event.key === " ")
        ) {
          event.preventDefault();
          selectRun(run);
        }
      });
      body.append(row);
    }
    set("usage-run-count", `${evidence.length} records`);
    byId("usage-empty").hidden = !!evidence.length;
    byId("usage-empty").textContent =
      "No canonical records match this selection. This is not a claim of zero consumption.";
    set(
      "usage-page",
      evidence.length
        ? `${page * 100 + 1}–${Math.min(evidence.length, (page + 1) * 100)} of ${evidence.length}`
        : "No records",
    );
    byId("usage-previous").disabled = page === 0;
    byId("usage-next").disabled = (page + 1) * 100 >= evidence.length;
  };
  const svgNode = (tag, attributes = {}) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes))
      node.setAttribute(key, String(value));
    return node;
  };
  const chart = () => {
    const [from, to] = period();
    const grain = to - from <= 86400000 ? 3600000 : 86400000;
    const costs = new Map();
    for (const run of filtered)
      if (known(run.cost?.amount) && !run.duplicate_of)
        costs.set(costKey(run), true);
    const costHost = byId("usage-chart-cost-plot");
    costHost.replaceChildren();
    const selector = document.createElement("select");
    selector.setAttribute("aria-label", "Cost source and unit");
    if (!costs.has(series)) series = costs.keys().next().value || "";
    for (const key of costs.keys()) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = costLabel(key);
      selector.append(option);
    }
    selector.value = series;
    selector.addEventListener("change", () => {
      series = selector.value;
      selection = null;
      save();
      render();
    });
    costHost.append(selector);
    const points = new Map();
    for (const run of filtered) {
      if (
        !known(run.cost?.amount) ||
        costKey(run) !== series ||
        run.duplicate_of ||
        !Number.isFinite(stamp(run))
      )
        continue;
      const at = Math.floor(stamp(run) / grain) * grain;
      if (!points.has(at)) points.set(at, []);
      points.get(at).push(run);
    }
    const buckets = [...points].sort((a, b) => a[0] - b[0]);
    const svg = svgNode("svg", {
      viewBox: "0 0 640 130",
      "aria-label":
        "Cost timeline. Click a point; Shift-click extends the UTC range. Drag to select a range.",
    });
    const low = buckets[0]?.[0] || 0,
      high = buckets.at(-1)?.[0] || low;
    const max = Math.max(
      1e-12,
      ...buckets.map(([, runs]) => aggregate(runs).costs.get(series) || 0),
    );
    const x = (at) =>
      high === low ? 320 : 15 + ((at - low) / (high - low)) * 610;
    let previous = null;
    for (const [at, records] of buckets) {
      const amount = aggregate(records).costs.get(series) || 0,
        y = 112 - (amount / max) * 90;
      if (previous && at - previous.at === grain)
        svg.append(
          svgNode("line", {
            x1: previous.x,
            y1: previous.y,
            x2: x(at),
            y2: y,
            stroke: "currentColor",
          }),
        );
      const dot = svgNode("circle", {
        cx: x(at),
        cy: y,
        r: 6,
        fill: "currentColor",
        "data-period": at,
      });
      const label = `${new Date(at).toISOString()} ≤ timestamp < ${new Date(at + grain).toISOString()}`;
      bind(dot, records, label, {
        kind: "range",
        from: at,
        to: at + grain,
        series,
      });
      svg.append(dot);
      previous = { at, x: x(at), y };
    }
    let brush = null;
    svg.addEventListener("pointerdown", (event) => {
      if (buckets.length && event.target === svg) {
        brush = event.clientX;
        svg.setPointerCapture(event.pointerId);
      }
    });
    svg.addEventListener("pointerup", (event) => {
      if (brush === null) return;
      const bounds = svg.getBoundingClientRect();
      const at = (clientX) =>
        low +
        Math.max(
          0,
          Math.min(
            1,
            (clientX - bounds.left - (bounds.width * 15) / 640) /
              ((bounds.width * 610) / 640),
          ),
        ) *
          (high - low);
      const a = at(brush),
        b = at(event.clientX);
      brush = null;
      choose({
        kind: "range",
        from: Math.floor(Math.min(a, b) / grain) * grain,
        to: Math.floor(Math.max(a, b) / grain) * grain + grain,
        series,
      });
    });
    costHost.append(svg);
    if (!buckets.length) {
      const note = document.createElement("p");
      note.textContent = "No measured cost in this scope. Unknown is not zero.";
      costHost.append(note);
    }
    set(
      "usage-chart-cost-title",
      "Cost by recorded completion/update · gaps are unknown",
    );
    const heatHost = byId("usage-chart-heat-plot");
    heatHost.replaceChildren();
    const days = new Map();
    for (const run of filtered) {
      const at = Math.floor(stamp(run) / 86400000) * 86400000;
      if (!Number.isFinite(at)) continue;
      if (!days.has(at)) days.set(at, []);
      days.get(at).push(run);
    }
    const heat = document.createElement("div");
    heat.className = "usage-calendar";
    for (const [day, records] of [...days].sort((a, b) => a[0] - b[0])) {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.textContent = `${new Date(day).toISOString().slice(5, 10)} · ${records.length}`;
      bind(cell, records, new Date(day).toISOString().slice(0, 10) + " UTC", {
        kind: "range",
        from: day,
        to: day + 86400000,
      });
      heat.append(cell);
    }
    heatHost.append(heat);
    set(
      "usage-chart-heat-title",
      "Recorded UTC days · click; Shift-click extends range",
    );
  };
  const dimensions = () => {
    for (const [name, key] of Object.entries(groupField)) {
      const host = byId("usage-" + name);
      host.replaceChildren();
      const groups = new Map();
      for (const run of filtered) {
        const value = text(run[key]);
        if (!groups.has(value)) groups.set(value, []);
        groups.get(value).push(run);
      }
      for (const [value, records] of groups) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "usage-dimension-row";
        const total = aggregate(records);
        button.textContent = `${value} · ${total.runs} runs · ${fmt.format(total.tokens)} tokens (${total.tokenUnknown} unknown) · ${costsText(total)} · ${fmt.format(total.duration)} recorded seconds (${total.durationUnknown} unknown) · ${total.settlements} recorded settlements; accepted delivery unknown`;
        bind(button, records, value, { kind: "group", dimension: name, value });
        host.append(button);
      }
    }
    const [start, end] = period();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      set(
        "usage-comparison",
        "Select a finite period to compare the preceding period. Acceptance, revisions and quality remain unknown without admission evidence.",
      );
      return;
    }
    const previous = rows.filter(
      (run) =>
        matches(run) &&
        stamp(run) >= start - (end - start) &&
        stamp(run) < start,
    );
    const a = aggregate(filtered),
      b = aggregate(previous);
    set(
      "usage-comparison",
      `Current ${new Date(start).toISOString()} → ${new Date(end).toISOString()}: ${costsText(a)}; ${fmt.format(a.duration)} recorded seconds. Previous equal period: ${costsText(b)}; ${fmt.format(b.duration)} seconds (${b.durationUnknown} unknown). Known tokens ${fmt.format(a.tokens)} vs ${fmt.format(b.tokens)}; missing totals ${a.tokenUnknown} vs ${b.tokenUnknown}. No accepted-delivery ranking is inferred.`,
    );
  };
  const signals = () => {
    const host = byId("usage-signals");
    host.replaceChildren();
    growthIds = new Set();
    const rules = new Map();
    for (const run of filtered)
      for (const signal of run.signals || []) {
        const rule = signal.split(":")[0];
        if (!rules.has(rule)) rules.set(rule, { label: signal, records: [] });
        rules.get(rule).records.push(run);
      }
    const daily = new Map();
    for (const run of filtered)
      if (knownTokens(run.tokens?.tokens_total) && !run.duplicate_of) {
        const day = Math.floor(stamp(run) / 86400000) * 86400000;
        if (!daily.has(day)) daily.set(day, []);
        daily.get(day).push(run);
      }
    for (const [day, records] of daily) {
      const previous = daily.get(day - 86400000),
        total = aggregate(records).tokens;
      if (
        previous &&
        aggregate(previous).tokens > 0 &&
        total >= 1000000 &&
        total >= 4 * aggregate(previous).tokens
      ) {
        for (const run of records) growthIds.add(run.run_id);
        if (!rules.has("growth"))
          rules.set("growth", {
            label:
              "growth: known tokens >=4× previous recorded UTC day and >=1M; unequal completeness may explain growth",
            records: [],
          });
        rules.get("growth").records.push(...records);
      }
    }
    for (const [rule, item] of rules) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${item.records.length} records · ${item.label}`;
      bind(button, item.records, item.label, { kind: "signal", rule });
      host.append(button);
    }
    if (!rules.size)
      host.textContent =
        "No configured signals in this scope. This does not certify healthy or efficient runs.";
  };
  const render = () => {
    if (!report) return;
    const [start, end] = period();
    if (Number.isNaN(start) || Number.isNaN(end) || start >= end) {
      set("usage-status", "Choose a valid UTC range: from < until.");
      return;
    }
    filtered = rows.filter(
      (run) => matches(run) && stamp(run) >= start && stamp(run) < end,
    );
    signals();
    evidence = filtered.filter(selectionMatches);
    const totals = aggregate(evidence);
    set("usage-total-runs", totals.runs);
    set("usage-total-failed", totals.failed);
    set(
      "usage-total-tokens",
      totals.runs ? `${fmt.format(totals.tokens)} measured` : "no records",
    );
    set(
      "usage-hero-tokens",
      totals.runs ? fmt.format(totals.tokens) : "no records",
    );
    set("usage-total-token-unknown", totals.tokenUnknown);
    set("usage-total-cost-unknown", totals.costUnknown);
    set("usage-total-cost", costsText(totals));
    set(
      "usage-hero-caption",
      `${totals.tokenUnknown} unknown totals · ${totals.duplicates} identical replays excluded · no invoice evidence`,
    );
    let selectionLabel = "";
    if (selection?.kind === "range") {
      selectionLabel =
        Number.isFinite(selection.from) && Number.isFinite(selection.to)
          ? `UTC range ${new Date(selection.from).toISOString()} → ${new Date(selection.to).toISOString()} (exclusive)`
          : "Invalid range; clear selection";
      if (selection.series) {
        try {
          selectionLabel += " · " + costLabel(selection.series);
        } catch (_) {
          selectionLabel += " · source unknown";
        }
      }
    } else if (selection?.kind === "group")
      selectionLabel = `${selection.dimension}: ${selection.value}`;
    else if (selection?.kind === "signal")
      selectionLabel = `Attention: ${selection.rule}`;
    set(
      "usage-selection",
      selection
        ? `${selectionLabel} · ${evidence.length} exact records · summary above covers the full filtered period`
        : `${filtered.length} matching records · click a point/group/rule; Shift-click extends time selection`,
    );
    set(
      "usage-generated",
      `Refreshed ${report.generated_at}; source: canonical runtime metadata and owner recovery. Run totals assigned to completion/update; within-run timing unknown.`,
    );
    set("usage-schema", report.schema);
    set(
      "usage-status",
      `${rows.length} canonical records loaded · ${filtered.length} in scope · ${report.coverage?.metadata_records_unreadable ?? "unknown"} metadata records not projected · ${report.coverage?.inventory_available === false ? "canonical inventory unavailable" : "missing data and overlapping intervals remain visible"}`,
    );
    renderRows();
    chart();
    dimensions();
    showView(view);
    showDimension(dimension);
    if (selected) {
      const run = evidence.find((run) => run.run_id === selected);
      if (run) selectRun(run);
    }
  };
  const load = async () => {
    const sequence = ++loadSequence;
    set("usage-status", "Reading canonical evidence…");
    try {
      const response = await fetch("/api/usage?window=all", {
        cache: "no-store",
      });
      const payload = await response.json();
      if (sequence !== loadSequence) return;
      if (!response.ok)
        throw new Error(payload.error || "HTTP " + response.status);
      report = payload;
      rows = report.runs || [];
      for (const [field, key] of [
        ["project", "root"],
        ["task", "task"],
      ]) {
        const host = byId("usage-" + field + "-options");
        host.replaceChildren();
        for (const value of new Set(
          rows.map((run) => run[key]).filter(Boolean),
        )) {
          const option = document.createElement("option");
          option.value = value;
          host.append(option);
        }
      }
      render();
    } catch (error) {
      if (sequence === loadSequence) {
        set(
          "usage-status",
          `Usage unavailable: ${error.message}${report ? "; retained data is stale" : ""}`,
        );
        set(
          "usage-hover",
          "Canonical evidence could not be refreshed. No zeros were substituted.",
        );
      }
    }
  };
  root
    .querySelectorAll("[data-usage-view]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        showView(button.dataset.usageView),
      ),
    );
  root
    .querySelectorAll("[data-usage-dim]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        showDimension(button.dataset.usageDim),
      ),
    );
  root
    .querySelectorAll("[data-usage-doc]")
    .forEach((button) =>
      button.addEventListener("click", () => showDoc(button.dataset.usageDoc)),
    );
  byId("usage-clear-selection").addEventListener("click", () => choose(null));
  byId("usage-close-inspector").addEventListener("click", () => {
    documentSequence++;
    selected = "";
    byId("overview-inspector").hidden = true;
    save();
  });
  byId("usage-next").addEventListener("click", () => {
    page++;
    renderRows();
    save();
  });
  byId("usage-previous").addEventListener("click", () => {
    page--;
    renderRows();
    save();
  });
  byId("usage-refresh").addEventListener("click", load);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    selection = null;
    selected = "";
    page = 0;
    save();
    render();
  });
  byId("usage-window").addEventListener("change", () => {
    const custom = byId("usage-window").value === "custom";
    byId("usage-from").disabled = !custom;
    byId("usage-to").disabled = !custom;
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) render();
  });
  byId("usage-window").dispatchEvent(new Event("change"));
  load();
})();
