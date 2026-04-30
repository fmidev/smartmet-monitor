// app.js — smartmet-webmon front-end. Multi-panel tab UI; one tab per
// curses panel from smtop. Vanilla JS, no build step. Polls the active
// panel's endpoints every REFRESH_MS.

(function () {
  "use strict";

  const REFRESH_MS = 2000;

  const state = {
    active: null,              // current panel id
    panels: {},                // per-panel local state
    poll: null,                // setInterval handle
  };

  // ---- DOM helpers ------------------------------------------------

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k === "events") for (const ev in attrs[k])
        e.addEventListener(ev, attrs[k][ev]);
      else if (attrs[k] !== undefined && attrs[k] !== null)
        e.setAttribute(k, attrs[k]);
    }
    for (const kid of kids) {
      if (kid == null) continue;
      e.appendChild(typeof kid === "string"
                    ? document.createTextNode(kid) : kid);
    }
    return e;
  }

  const escHtml = s => String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  // ---- formatters & color ----------------------------------------

  const fmtMs = smChart.formatMs;
  const fmtBytes = smChart.formatBytes;
  const fmtCount = smChart.formatCount;
  function latColor(ms) {
    if (ms == null) return "";
    if (ms < 100) return "lat-good";
    if (ms < 1000) return "lat-warn";
    return "lat-bad";
  }
  function errColor(pct) { return pct >= 1 ? "err-bad" : ""; }
  function cpuClass(ratio) {
    if (ratio == null || isNaN(ratio)) return "";
    if (ratio >= 0.5) return "cpu-good";
    if (ratio <= 0.1) return "cpu-wait";
    return "cpu-mix";
  }
  function fillClass(used, max) {
    if (!max) return "";
    const r = used / max;
    if (r >= 0.95) return "fill-bad";
    if (r >= 0.8) return "fill-warn";
    return "fill-good";
  }
  function hitrateClass(pct) {
    if (pct >= 90) return "fill-good";
    if (pct >= 50) return "fill-warn";
    return "fill-bad";
  }

  // ---- table renderer --------------------------------------------

  // columns: [{key, label, class, fmt(value, row), html(value, row)}]
  function renderTable(tbody, columns, rows, opts = {}) {
    const frag = document.createDocumentFragment();
    for (const r of rows) {
      const tr = el("tr", opts.onRowClick ? { class: "clickable" } : null);
      if (opts.selected && opts.selected(r)) tr.classList.add("selected");
      for (const c of columns) {
        const v = r[c.key];
        const td = document.createElement("td");
        if (c.class) td.className = c.class;
        if (c.html) td.innerHTML = c.html(v, r);
        else if (c.fmt) td.textContent = c.fmt(v, r);
        else td.textContent = v == null ? "" : String(v);
        if (c.color) {
          const cls = c.color(v, r);
          if (cls) td.classList.add(cls);
        }
        if (c.title) td.title = c.title(v, r);
        tr.appendChild(td);
      }
      if (opts.onRowClick) tr.addEventListener("click",
                                                () => opts.onRowClick(r));
      if (opts.afterRow) opts.afterRow(tr, r);
      frag.appendChild(tr);
    }
    tbody.replaceChildren(frag);
  }

  // ---- small inputs ----------------------------------------------

  function selectInput(id, value, options, onChange) {
    const sel = el("select", { id });
    for (const o of options) {
      const opt = el("option", { value: String(o.value) }, o.label);
      if (String(o.value) === String(value)) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => onChange(sel.value));
    return sel;
  }
  function textInput(id, value, placeholder, onChange) {
    const t = el("input", { type: "search", id, placeholder });
    t.value = value || "";
    let timer = null;
    t.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => onChange(t.value), 250);
    });
    return t;
  }
  function toggleButton(label, active, onChange) {
    const b = el("button", { class: "btn toggle" + (active ? " on" : "") },
                  label);
    b.addEventListener("click", () => {
      active = !active;
      b.classList.toggle("on", active);
      onChange(active);
    });
    return b;
  }

  // ---- HTTP -------------------------------------------------------

  async function getJSON(path, params) {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    const r = await fetch(path + qs, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status} for ${path}`);
    return r.json();
  }

  function setIndicator(text, cls) {
    const el = document.getElementById("refresh-indicator");
    el.textContent = text;
    el.className = "indicator " + (cls || "");
  }

  // ---- modal ------------------------------------------------------

  const modal = {
    overlay: () => document.getElementById("modal-overlay"),
    title:   () => document.getElementById("modal-title"),
    body:    () => document.getElementById("modal-body"),
    open(title) {
      modal.title().textContent = title;
      modal.body().innerHTML = "";
      modal.overlay().classList.remove("hidden");
      document.body.style.overflow = "hidden";
    },
    close() {
      modal.overlay().classList.add("hidden");
      document.body.style.overflow = "";
      state.modalRefresh = null;
    },
  };
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("modal-close").addEventListener(
      "click", () => modal.close());
    modal.overlay().addEventListener("click", e => {
      if (e.target === modal.overlay()) modal.close();
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape") modal.close();
    });
  });

  // =================================================================
  // Panels
  // =================================================================

  const PANELS = {};

  // -- URLs ---------------------------------------------------------

  PANELS.urls = (function () {
    const ps = state.panels.urls = {
      window: 5, sort: "p95", reverse: true, filter: "",
      selected: null,
    };
    let tbody;
    return {
      title: "URLs",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        const controls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "URLs"),
          el("label", null, "window",
            selectInput("urls-window", ps.window,
              [1,5,15,60].map(m => ({value: m, label: m + "m"})),
              v => { ps.window = +v; refresh(); })),
          el("label", null, "sort",
            selectInput("urls-sort", ps.sort,
              [["count","reqs"],["p95","p95"],["p50","p50"],
               ["mean_ms","mean"],["max_ms","max"],
               ["mb_tot","total bytes"],["err_pct","err%"]].map(
                ([v,l]) => ({value: v, label: l})),
              v => { ps.sort = v; refresh(); })),
          el("label", null, "filter",
            textInput("urls-filter", ps.filter, "substring",
                      v => { ps.filter = v; refresh(); })),
          el("span", { id: "urls-count", class: "muted" }),
        );
        const table = el("table", { class: "data-table" },
          el("thead", null, el("tr", null,
            ...["URL","reqs","mean","p50","p95","p99","max","avg sz","total","err%"]
              .map((h,i) => el("th",
                { class: i === 0 ? "url" : "num" }, h)))),
          el("tbody"));
        tbody = table.querySelector("tbody");
        panel.appendChild(controls);
        panel.appendChild(table);
        host.appendChild(panel);
      },
      async refresh() { await refresh(); },
    };
    async function refresh() {
      const data = await getJSON("/api/urls", {
        window: ps.window, sort: ps.sort,
        reverse: ps.reverse ? 1 : 0, filter: ps.filter,
      });
      document.getElementById("urls-count").textContent =
        `${data.rows.length} URL${data.rows.length === 1 ? "" : "s"}`;
      renderTable(tbody, [
        { key: "url", class: "url",
          html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
        { key: "count",       class: "num", fmt: fmtCount },
        { key: "mean_ms",     class: "num", fmt: fmtMs, color: latColor },
        { key: "p50_ms",      class: "num", fmt: fmtMs, color: latColor },
        { key: "p95_ms",      class: "num", fmt: fmtMs, color: latColor },
        { key: "p99_ms",      class: "num", fmt: fmtMs, color: latColor },
        { key: "max_ms",      class: "num", fmt: fmtMs, color: latColor },
        { key: "avg_bytes",   class: "num", fmt: fmtBytes },
        { key: "total_bytes", class: "num", fmt: fmtBytes },
        { key: "err_pct",     class: "num",
          fmt: v => v.toFixed(1), color: errColor },
      ], data.rows, {
        onRowClick: r => openUrlDetail(r.url),
        selected: r => r.url === ps.selected,
      });
    }
    async function openUrlDetail(url) {
      ps.selected = url;
      modal.open(url);
      const body = modal.body();
      body.innerHTML = `
        <h3>Windowed stats</h3>
        <table class="data-table"><thead><tr>
          <th>window</th><th class="num">reqs</th><th class="num">mean</th>
          <th class="num">p50</th><th class="num">p95</th>
          <th class="num">p99</th><th class="num">max</th>
          <th class="num">avg sz</th><th class="num">total</th>
          <th class="num">err%</th>
        </tr></thead><tbody></tbody></table>
        <h3>Mean latency, last 60 min</h3>
        <canvas class="chart" data-role="line" height="160"></canvas>
        <h3>Latency distribution</h3>
        <canvas class="chart" data-role="hist" height="200"></canvas>
        <h3>Status codes</h3>
        <table class="data-table" id="d-status"><thead><tr>
          <th>code</th><th class="num">count</th><th class="num">%</th>
        </tr></thead><tbody></tbody></table>
        <h3>Top API keys</h3>
        <table class="data-table" id="d-keys"><thead><tr>
          <th>apikey</th><th class="num">requests</th>
        </tr></thead><tbody></tbody></table>
      `;
      const refreshDetail = async () => {
        const [d, c] = await Promise.all([
          getJSON("/api/urls/detail", { url, window: ps.window }),
          getJSON("/api/urls/chart",  { url, window: 60 }),
        ]);
        const tb = body.querySelector("table tbody");
        const winCols = [
          { key: "window_min", fmt: v => v + "m" },
          { key: "count",      class: "num", fmt: fmtCount },
          { key: "mean_ms",    class: "num", fmt: fmtMs, color: latColor },
          { key: "p50_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "p95_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "p99_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "max_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "avg_bytes",  class: "num", fmt: fmtBytes },
          { key: "total_bytes",class: "num", fmt: fmtBytes },
          { key: "err_pct",    class: "num",
            fmt: v => v.toFixed(1), color: errColor },
        ];
        renderTable(tb, winCols, (d.windows || []));
        const cs = body.querySelectorAll("canvas");
        smChart.drawLine(cs[0], (c.values || []).map(Number),
                          { xLabels: ["-60m", "now"] });
        smChart.drawHistogram(cs[1], d.histogram ? d.histogram.buckets : []);
        renderTable(body.querySelector("#d-status tbody"), [
          { key: "status",
            html: v => `<span class="${
              v >= 500 ? 'err-bad' : v >= 400 ? 'lat-warn' : 'lat-good'
            }">${v}</span>` },
          { key: "count", class: "num", fmt: fmtCount },
          { key: "pct",   class: "num", fmt: v => v.toFixed(1) },
        ], d.status_codes || []);
        renderTable(body.querySelector("#d-keys tbody"), [
          { key: "key" },
          { key: "count", class: "num", fmt: fmtCount },
        ], d.apikeys || []);
      };
      state.modalRefresh = refreshDetail;
      await refreshDetail();
    }
  })();

  // -- Plugins ------------------------------------------------------

  PANELS.plugins = (function () {
    const ps = state.panels.plugins = {
      window: "60s", sort: "rps", reverse: true, filter: "",
      hide_idle: true,
    };
    let tbody;
    return {
      title: "Plugins",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Plugins"),
          el("label", null, "window",
            selectInput("p-window", ps.window,
              [["60s","60s"],["1m","1m"],["5m","5m"],["15m","15m"],["60m","60m"]]
                .map(([v,l]) => ({value:v,label:l})),
              v => { ps.window = v; refresh(); })),
          el("label", null, "sort",
            selectInput("p-sort", ps.sort,
              [["name","name"],["rps","req/s"],["mean_ms","mean"],
               ["p95_ms","p95"],["err_pct","err%"],["bytes","bytes"]]
                .map(([v,l]) => ({value:v,label:l})),
              v => { ps.sort = v; refresh(); })),
          el("label", null, "filter",
            textInput("p-filter", ps.filter, "plugin name",
                      v => { ps.filter = v; refresh(); })),
          toggleButton("hide idle", ps.hide_idle,
                        v => { ps.hide_idle = v; refresh(); }),
        );
        const table = el("table", { class: "data-table" },
          el("thead", null, el("tr", null,
            ...["plugin","req/s","mean","p50","p95","max","B/s","err%","reqs",
                "latency","size"]
              .map((h,i) => el("th",
                { class: i === 0 ? "" : (i >= 9 ? "spark" : "num") }, h)))),
          el("tbody"));
        tbody = table.querySelector("tbody");
        panel.appendChild(ctrls);
        panel.appendChild(table);
        host.appendChild(panel);
      },
      async refresh() { await refresh(); },
    };
    async function refresh() {
      const [data, trends] = await Promise.all([
        getJSON("/api/plugins", {
          window: ps.window, sort: ps.sort,
          reverse: ps.reverse ? 1 : 0, filter: ps.filter,
          hide_idle: ps.hide_idle ? 1 : 0,
        }),
        getJSON("/api/plugins/trends", {
          window: ps.window, filter: ps.filter,
          hide_idle: ps.hide_idle ? 1 : 0,
        }),
      ]);
      const trendByLabel = new Map(
        (trends.rows || []).map(r => [r.label, r.metrics]));
      // Stash a per-row sparkline placeholder, then paint after render.
      const cols = [
        { key: "plugin",          class: "handler",
          html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
        { key: "rps_60s",         class: "num", fmt: v => v.toFixed(1) },
        { key: "mean_ms_60s",     class: "num", fmt: fmtMs, color: latColor },
        { key: "p50_ms_60s",      class: "num", fmt: fmtMs, color: latColor },
        { key: "p95_ms_60s",      class: "num", fmt: fmtMs, color: latColor },
        { key: "max_ms_60s",      class: "num", fmt: fmtMs, color: latColor },
        { key: "bytes_per_sec_60s", class: "num", fmt: fmtBytes },
        { key: "err_pct_60s",     class: "num",
          fmt: v => v.toFixed(1), color: errColor },
        { key: "requests_60s",    class: "num", fmt: fmtCount },
        { key: "_lat_spark",      class: "spark",
          html: () => `<canvas data-role="lat-spark"></canvas>` },
        { key: "_size_spark",     class: "spark",
          html: () => `<canvas data-role="size-spark"></canvas>` },
      ];
      renderTable(tbody, cols, data.rows || [], {
        afterRow(tr, r) {
          const m = trendByLabel.get(r.plugin);
          if (!m) return;
          const lat = tr.querySelector('canvas[data-role="lat-spark"]');
          const sz  = tr.querySelector('canvas[data-role="size-spark"]');
          // Draw after a microtask so the canvas has a measured layout.
          requestAnimationFrame(() => {
            smChart.drawSparkline(lat, m.mean_ms || [],
                                  { color: smChart.PALETTE.line });
            smChart.drawSparkline(sz, m.bytes_mean || [],
                                  { color: smChart.PALETTE.accent2 });
          });
        },
      });
    }
  })();

  // -- Overview -----------------------------------------------------

  PANELS.overview = (function () {
    let tbody, charts = {};
    return {
      title: "Overview",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" },
          el("div", { class: "subpanel-title" }, "Totals"),
          el("table", { class: "data-table" },
            el("thead", null, el("tr", null,
              ...["window","reqs","mean","p50","p95","max","total bytes","err%"]
                .map(h => el("th", { class: "num" }, h)))),
            el("tbody")),
        );
        tbody = panel.querySelector("tbody");
        host.appendChild(panel);

        const grid = el("div", { class: "section-grid" });
        for (const [m, label] of [
          ["count",   "req/min"],
          ["mean_ms", "mean ms"],
          ["p95_ms",  "p95 ms"],
          ["bytes",   "bytes/min"],
          ["err_pct", "err %"],
        ]) {
          const card = el("div", { class: "section-card" },
            el("h4", null, label),
            el("canvas", { class: "chart", "data-metric": m, height: "120" }));
          grid.appendChild(card);
          charts[m] = card.querySelector("canvas");
        }
        const wrap = el("section", { class: "panel" },
          el("div", { class: "subpanel-title" }, "History"), grid);
        host.appendChild(wrap);
      },
      async refresh() {
        const data = await getJSON("/api/overview");
        renderTable(tbody, [
          { key: "window_min", class: "num", fmt: v => v + "m" },
          { key: "reqs",       class: "num", fmt: fmtCount },
          { key: "mean_ms",    class: "num", fmt: fmtMs, color: latColor },
          { key: "p50_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "p95_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "max_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "total_bytes",class: "num", fmt: fmtBytes },
          { key: "err_pct",    class: "num",
            fmt: v => v.toFixed(1), color: errColor },
        ], data.rows || []);

        for (const m of Object.keys(charts)) {
          const c = await getJSON("/api/overview/chart", { metric: m });
          const fmtY = (m === "bytes") ? fmtBytes
                      : (m === "count" || m === "err_pct")
                        ? v => Math.round(v) : fmtMs;
          smChart.drawLine(charts[m], (c.values || []).map(Number),
                            { fmtY });
        }
      },
    };
  })();

  // -- Caches -------------------------------------------------------

  PANELS.caches = (function () {
    let tbody;
    return {
      title: "Caches",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" },
          el("div", { class: "panel-controls" },
            el("span", { class: "panel-title" }, "Caches")),
          el("table", { class: "data-table" },
            el("thead", null, el("tr", null,
              ...["host","cache","size","max","hits/m","ins/m","hit%",
                  "fill","hits trend"]
                .map((h,i) => el("th",
                  { class: i === 0 || i === 1 ? "" :
                            (i === 7 || i === 8) ? "spark" : "num" }, h)))),
            el("tbody")));
        tbody = panel.querySelector("tbody");
        host.appendChild(panel);
      },
      async refresh() {
        const [data, trends] = await Promise.all([
          getJSON("/api/caches"),
          getJSON("/api/caches/trends"),
        ]);
        const tBy = new Map((trends.rows || [])
          .map(r => [`${r.host}::${r.cache_name}`, r.values]));
        renderTable(tbody, [
          { key: "host" },
          { key: "cache_name", class: "handler",
            html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
          { key: "size",            class: "num", fmt: fmtCount,
            color: (_, r) => fillClass(r.size, r.maxsize) },
          { key: "maxsize",         class: "num", fmt: fmtCount },
          { key: "hits_per_min",    class: "num", fmt: v => v.toFixed(1) },
          { key: "inserts_per_min", class: "num", fmt: v => v.toFixed(1) },
          { key: "hitrate_pct",     class: "num",
            fmt: v => v.toFixed(1) + "%",
            color: v => hitrateClass(v) },
          { key: "_fill", class: "bar spark",
            html: (_, r) => `<div class="hbar"><div class="hbar-fill ${
              hitrateClass(r.hitrate_pct)
            }" style="width:${Math.max(0, Math.min(100, r.hitrate_pct))}%"></div></div>` },
          { key: "_trend", class: "spark",
            html: () => `<canvas data-role="trend"></canvas>` },
        ], data.rows || [], {
          afterRow(tr, r) {
            const c = tr.querySelector('canvas[data-role="trend"]');
            const series = tBy.get(`${r.host}::${r.cache_name}`) || [];
            requestAnimationFrame(() => smChart.drawSparkline(c, series));
          },
        });
      },
    };
  })();

  // -- Services -----------------------------------------------------

  PANELS.services = (function () {
    let tbody;
    return {
      title: "Services",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" },
          el("div", { class: "panel-controls" },
            el("span", { class: "panel-title" }, "Services")),
          el("table", { class: "data-table" },
            el("thead", null, el("tr", null,
              ...["host","handler","req/min","req/h","req/d","avg","cpu%","trend"]
                .map((h,i) => el("th",
                  { class: i === 0 || i === 1 ? "" :
                            (i === 7) ? "spark" : "num" }, h)))),
            el("tbody")));
        tbody = panel.querySelector("tbody");
        host.appendChild(panel);
      },
      async refresh() {
        const [data, trends] = await Promise.all([
          getJSON("/api/services"),
          getJSON("/api/services/trends"),
        ]);
        const tBy = new Map((trends.rows || [])
          .map(r => [`${r.host}::${r.handler}`, r.values]));
        renderTable(tbody, [
          { key: "host" },
          { key: "handler", class: "handler",
            html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
          { key: "req_per_min",  class: "num", fmt: v => v.toFixed(0) },
          { key: "req_per_hour", class: "num", fmt: v => v.toFixed(0) },
          { key: "req_per_day",  class: "num", fmt: v => v.toFixed(0) },
          { key: "avg_ms",       class: "num", fmt: fmtMs, color: latColor },
          { key: "_cpu",         class: "num",
            html: (_, r) => {
              if (!r.avg_ms || !r.avg_cpu_ms) return '<span class="muted">—</span>';
              const ratio = r.avg_cpu_ms / r.avg_ms;
              const cls = cpuClass(ratio);
              return `<span class="${cls}">${(ratio * 100).toFixed(0)}%</span>`;
            } },
          { key: "_trend", class: "spark",
            html: () => `<canvas data-role="trend"></canvas>` },
        ], data.rows || [], {
          afterRow(tr, r) {
            const c = tr.querySelector('canvas[data-role="trend"]');
            const series = tBy.get(`${r.host}::${r.handler}`) || [];
            requestAnimationFrame(() => smChart.drawSparkline(c, series));
          },
        });
      },
    };
  })();

  // -- Active -------------------------------------------------------

  PANELS.active = (function () {
    let chartCanvas, tbody, headEl;
    return {
      title: "Active",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        headEl = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Active in-flight requests"),
          el("span", { class: "muted", id: "active-summary" }));
        const chartCard = el("div", { class: "section-card" },
          el("h4", null, "in-flight count"),
          el("canvas", { class: "chart", height: "120" }));
        chartCanvas = chartCard.querySelector("canvas");
        const grid = el("div", { class: "section-grid" }, chartCard);
        const table = el("table", { class: "data-table" },
          el("thead", null, el("tr", null,
            ...["host","id","dur","client","apikey","request"]
              .map((h,i) => el("th",
                { class: i === 5 ? "url" : (i === 1 || i === 2) ? "num" : "" },
                h)))),
          el("tbody"));
        tbody = table.querySelector("tbody");
        panel.appendChild(headEl);
        panel.appendChild(grid);
        panel.appendChild(table);
        host.appendChild(panel);
      },
      async refresh() {
        const [tab, ch] = await Promise.all([
          getJSON("/api/active"),
          getJSON("/api/active/chart"),
        ]);
        document.getElementById("active-summary").textContent =
          `current ${ch.current}, peak ${ch.peak}`;
        smChart.drawLine(chartCanvas, ch.values || [],
                          { fmtY: v => Math.round(v) });
        renderTable(tbody, [
          { key: "host" },
          { key: "id",          class: "num" },
          { key: "duration_s",  class: "num", fmt: v => v.toFixed(1),
            color: v => latColor(v * 1000) },
          { key: "client_ip" },
          { key: "apikey" },
          { key: "request",     class: "url",
            html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
        ], tab.rows || []);
      },
    };
  })();

  // -- Keys ---------------------------------------------------------

  PANELS.keys = (function () {
    const ps = state.panels.keys = {
      window: 60, sort: "count", reverse: true, filter: "",
    };
    let tbody;
    return {
      title: "API Keys",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "API Keys"),
          el("label", null, "window",
            selectInput("k-window", ps.window,
              [1,5,15,60].map(m => ({value:m,label:m+"m"})),
              v => { ps.window = +v; refresh(); })),
          el("label", null, "sort",
            selectInput("k-sort", ps.sort,
              [["count","reqs"],["p95","p95"],["mean_ms","mean"],
               ["mb_tot","total bytes"],["err_pct","err%"],["key_asc","key"]]
                .map(([v,l]) => ({value:v,label:l})),
              v => { ps.sort = v; refresh(); })),
          el("label", null, "filter",
            textInput("k-filter", ps.filter, "key substring",
                      v => { ps.filter = v; refresh(); })),
        );
        const table = el("table", { class: "data-table" },
          el("thead", null, el("tr", null,
            ...["apikey","reqs","mean","p50","p95","max","bytes","err%"]
              .map((h,i) => el("th",
                { class: i === 0 ? "url" : "num" }, h)))),
          el("tbody"));
        tbody = table.querySelector("tbody");
        panel.appendChild(ctrls);
        panel.appendChild(table);
        host.appendChild(panel);
      },
      async refresh() { await refresh(); },
    };
    async function refresh() {
      const data = await getJSON("/api/keys", {
        window: ps.window, sort: ps.sort,
        reverse: ps.reverse ? 1 : 0, filter: ps.filter,
      });
      renderTable(tbody, [
        { key: "apikey", class: "url",
          html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
        { key: "count",       class: "num", fmt: fmtCount },
        { key: "mean_ms",     class: "num", fmt: fmtMs, color: latColor },
        { key: "p50_ms",      class: "num", fmt: fmtMs, color: latColor },
        { key: "p95_ms",      class: "num", fmt: fmtMs, color: latColor },
        { key: "max_ms",      class: "num", fmt: fmtMs, color: latColor },
        { key: "total_bytes", class: "num", fmt: fmtBytes },
        { key: "err_pct",     class: "num",
          fmt: v => v.toFixed(1), color: errColor },
      ], data.rows || [], {
        onRowClick: r => openKeyDetail(r.apikey),
      });
    }
    async function openKeyDetail(apikey) {
      modal.open("apikey: " + apikey);
      const body = modal.body();
      body.innerHTML = `
        <h3>Windowed stats</h3>
        <table class="data-table"><thead><tr>
          <th>window</th><th class="num">reqs</th><th class="num">mean</th>
          <th class="num">p50</th><th class="num">p95</th>
          <th class="num">max</th><th class="num">bytes</th>
          <th class="num">err%</th>
        </tr></thead><tbody></tbody></table>
        <h3>Top URLs hit by this key</h3>
        <table class="data-table" id="d-urls"><thead><tr>
          <th>URL</th><th class="num">requests</th>
        </tr></thead><tbody></tbody></table>
      `;
      const refreshDetail = async () => {
        const d = await getJSON("/api/keys/detail",
                                  { apikey, window: ps.window });
        const tb = body.querySelector("table tbody");
        renderTable(tb, [
          { key: "window_min", fmt: v => v + "m" },
          { key: "count",      class: "num", fmt: fmtCount },
          { key: "mean_ms",    class: "num", fmt: fmtMs, color: latColor },
          { key: "p50_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "p95_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "max_ms",     class: "num", fmt: fmtMs, color: latColor },
          { key: "total_bytes",class: "num", fmt: fmtBytes },
          { key: "err_pct",    class: "num",
            fmt: v => v.toFixed(1), color: errColor },
        ], d.windows || []);
        renderTable(body.querySelector("#d-urls tbody"), [
          { key: "url", class: "url",
            html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
          { key: "count", class: "num", fmt: fmtCount },
        ], d.urls || []);
      };
      state.modalRefresh = refreshDetail;
      await refreshDetail();
    }
  })();

  // -- Proc ---------------------------------------------------------

  PANELS.proc = (function () {
    const ps = state.panels.proc = { pid: null };
    let pidSel, body;
    return {
      title: "Proc",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Process"),
          el("label", null, "pid",
            (pidSel = el("select", { id: "proc-pid" }))));
        pidSel.addEventListener("change",
          () => { ps.pid = +pidSel.value; refresh(); });
        body = el("div");
        panel.appendChild(ctrls);
        panel.appendChild(body);
        host.appendChild(panel);
      },
      async refresh() {
        const [pids, det] = await Promise.all([
          getJSON("/api/proc/pids"),
          getJSON("/api/proc/detail",
            ps.pid != null ? { pid: ps.pid } : {}),
        ]);
        // Keep the PID selector in sync.
        const opts = (pids.pids || []).map(
          p => `<option value="${p.pid}">${p.pid} (${p.role}) ${
            escHtml(p.cmdline)}</option>`).join("");
        if (pidSel.innerHTML !== opts) pidSel.innerHTML = opts;
        if (det.found) pidSel.value = String(det.pid);
        else if (pids.default) pidSel.value = String(pids.default);

        if (!det.found) {
          body.innerHTML =
            '<div class="panel-empty">no smartmetd processes found yet</div>';
          return;
        }
        const L = det.latest;
        const S = det.series;
        body.innerHTML = `
          <div class="section-grid">
            <div class="section-card">
              <h4>Memory</h4>
              <div class="kv">
                <span class="k">VM RSS</span><span class="v">${
                  fmtBytes((L.vm_rss_kb || 0) * 1024)}</span>
                <span class="k">RSS anon</span><span class="v">${
                  fmtBytes((L.rss_anon_kb || 0) * 1024)}</span>
                <span class="k">RSS file</span><span class="v">${
                  fmtBytes((L.rss_file_kb || 0) * 1024)}</span>
                <span class="k">RSS shmem</span><span class="v">${
                  fmtBytes((L.rss_shmem_kb || 0) * 1024)}</span>
                <span class="k">VM size</span><span class="v">${
                  fmtBytes((L.vm_size_kb || 0) * 1024)}</span>
                <span class="k">VM swap</span><span class="v">${
                  fmtBytes((L.vm_swap_kb || 0) * 1024)}</span>
                <span class="k">VM HWM</span><span class="v">${
                  fmtBytes((L.vm_hwm_kb || 0) * 1024)}</span>
              </div>
              <canvas class="chart" data-role="rss" height="100"></canvas>
            </div>
            <div class="section-card">
              <h4>I/O (rate)</h4>
              <div class="kv">
                <span class="k">read total</span><span class="v">${
                  fmtBytes(L.io_read_bytes)}</span>
                <span class="k">write total</span><span class="v">${
                  fmtBytes(L.io_write_bytes)}</span>
              </div>
              <canvas class="chart" data-role="io" height="100"></canvas>
            </div>
            <div class="section-card">
              <h4>Threads &amp; FDs</h4>
              <div class="kv">
                <span class="k">threads</span><span class="v">${L.threads}</span>
                <span class="k">fds</span><span class="v">${L.fds}</span>
              </div>
              <canvas class="chart" data-role="threads" height="100"></canvas>
            </div>
            <div class="section-card">
              <h4>Major page-fault rate</h4>
              <canvas class="chart" data-role="majflt" height="100"></canvas>
            </div>
          </div>
        `;
        const c = sel => body.querySelector(`canvas[data-role="${sel}"]`);
        smChart.drawLine(c("rss"),
          (S.vm_rss_kb || []).map(v => v * 1024),
          { fmtY: fmtBytes });
        // Compute combined IO bytes/s by stacking read/write — show as
        // two lines on one chart by drawing twice with different colors.
        smChart.drawLine(c("io"), S.io_read_bps || [],
          { fmtY: fmtBytes, lineColor: smChart.PALETTE.line });
        smChart.drawLine(c("threads"), S.threads || [],
          { fmtY: v => Math.round(v) });
        smChart.drawLine(c("majflt"), S.majflt_per_s || [],
          { fmtY: v => v.toFixed(2) });
      },
    };
  })();

  // -- Network ------------------------------------------------------

  PANELS.network = (function () {
    let body;
    return {
      title: "Network",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" },
          el("div", { class: "panel-controls" },
            el("span", { class: "panel-title" }, "Network")));
        body = el("div");
        panel.appendChild(body);
        host.appendChild(panel);
      },
      async refresh() {
        const d = await getJSON("/api/network/detail");
        if (!d.enabled) {
          body.innerHTML =
            '<div class="panel-empty">netstats sampler not started yet</div>';
          return;
        }
        const sum = d.tcp_summary;
        const cards = [];
        cards.push(`
          <div class="section-card">
            <h4>TCP host-wide</h4>
            <div class="kv">
              <span class="k">retrans/s</span><span class="v">${sum.retrans_latest.toFixed(2)}</span>
              <span class="k">listen overflow/s</span><span class="v">${sum.overflow_latest.toFixed(2)}</span>
              <span class="k">listen drop/s</span><span class="v">${sum.drop_latest.toFixed(2)}</span>
            </div>
            <canvas class="chart" data-tcp="retrans" height="80"></canvas>
          </div>
        `);
        cards.push(`
          <div class="section-card">
            <h4>Connection states</h4>
            <table class="data-table"><thead><tr>
              <th>state</th><th class="num">count</th><th class="spark">trend</th>
            </tr></thead><tbody>${
              d.states.map(s => `
                <tr>
                  <td>${escHtml(s.state)}</td>
                  <td class="num">${fmtCount(s.count)}</td>
                  <td class="spark"><canvas data-state="${escHtml(s.state)}"></canvas></td>
                </tr>
              `).join("")
            }</tbody></table>
          </div>
        `);
        cards.push(`
          <div class="section-card">
            <h4>Listen sockets (port — recv-Q)</h4>
            <table class="data-table"><thead><tr>
              <th class="num">port</th><th class="num">recv-Q</th>
            </tr></thead><tbody>${
              d.listen_sockets.map(l => `
                <tr><td class="num">${l.port}</td>
                <td class="num ${l.recv_q > 0 ? 'lat-warn' : ''}">${l.recv_q}</td></tr>
              `).join("") || '<tr><td colspan="2" class="muted">none</td></tr>'
            }</tbody></table>
          </div>
        `);
        cards.push(`
          <div class="section-card">
            <h4>Per-NIC bandwidth (B/s)</h4>
            ${d.ifaces.map(f => `
              <div style="margin-top:6px">
                <strong>${escHtml(f.iface)}</strong>
                <span class="muted">  rx ${fmtBytes(f.rx_latest)}/s  tx ${fmtBytes(f.tx_latest)}/s</span>
                <canvas class="chart" data-iface="${escHtml(f.iface)}" data-dir="rx" height="44"></canvas>
                <canvas class="chart" data-iface="${escHtml(f.iface)}" data-dir="tx" height="44"></canvas>
              </div>
            `).join("") || '<div class="muted">no interfaces</div>'}
          </div>
        `);
        body.innerHTML = `<div class="section-grid">${cards.join("")}</div>`;

        // Now paint canvases.
        smChart.drawLine(body.querySelector('canvas[data-tcp="retrans"]'),
                          sum.retrans_per_s || [],
                          { fmtY: v => v.toFixed(1) });
        for (const s of d.states) {
          const c = body.querySelector(
            `canvas[data-state="${cssEsc(s.state)}"]`);
          if (c) smChart.drawSparkline(c, s.trend || []);
        }
        for (const f of d.ifaces) {
          const rx = body.querySelector(
            `canvas[data-iface="${cssEsc(f.iface)}"][data-dir="rx"]`);
          const tx = body.querySelector(
            `canvas[data-iface="${cssEsc(f.iface)}"][data-dir="tx"]`);
          if (rx) smChart.drawLine(rx, f.rx_bps || [],
                                    { fmtY: fmtBytes,
                                      lineColor: smChart.PALETTE.good });
          if (tx) smChart.drawLine(tx, f.tx_bps || [],
                                    { fmtY: fmtBytes,
                                      lineColor: smChart.PALETTE.warn });
        }
      },
    };
  })();

  function cssEsc(s) {
    if (CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  // -- Flame --------------------------------------------------------

  PANELS.flame = (function () {
    const ps = state.panels.flame = {
      pid: null, mode: "on-cpu",
      smartmet_only: true, thread: "all", search: "",
    };
    let flame, modeBar, statusEl, pidSel, threadBar, smartmetBtn,
        searchBox, breadcrumbEl, topTbody;
    return {
      title: "Flame",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        statusEl = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Flame"));
        const ctrls = el("div", { class: "flame-controls" });
        modeBar = el("span", { class: "flame-modes" });
        ctrls.appendChild(el("label", null, "mode"));
        ctrls.appendChild(modeBar);
        ctrls.appendChild(el("label", null, "pid"));
        pidSel = el("select", { id: "flame-pid" });
        pidSel.addEventListener("change",
          () => { ps.pid = +pidSel.value; refresh(); });
        ctrls.appendChild(pidSel);
        ctrls.appendChild(el("label", null, "thread"));
        threadBar = el("span", { class: "flame-modes" });
        ctrls.appendChild(threadBar);
        smartmetBtn = el("button", {
          class: "btn toggle" + (ps.smartmet_only ? " on" : ""),
        }, "smartmet-only");
        smartmetBtn.addEventListener("click", () => {
          ps.smartmet_only = !ps.smartmet_only;
          smartmetBtn.classList.toggle("on", ps.smartmet_only);
          refresh();
        });
        ctrls.appendChild(smartmetBtn);
        ctrls.appendChild(el("label", null, "search",
          (searchBox = el("input", { type: "search",
                                      placeholder: "highlight" }))));
        searchBox.addEventListener("input", () => {
          ps.search = searchBox.value;
          if (flame) flame.setSearch(ps.search);
        });

        breadcrumbEl = el("div", { id: "flame-breadcrumb" });

        const canvas = el("canvas", { id: "flame-canvas" });
        const tooltip = document.getElementById("flame-tooltip");
        flame = new FlameView(canvas, tooltip);
        flame.onZoom = path => renderBreadcrumb(path);

        const topTable = el("table", { class: "data-table" },
          el("thead", null, el("tr", null,
            el("th", null, "%"), el("th", null, "function"),
            el("th", { class: "num" }, "weight"))),
          el("tbody"));
        topTbody = topTable.querySelector("tbody");

        panel.appendChild(statusEl);
        panel.appendChild(ctrls);
        panel.appendChild(breadcrumbEl);
        panel.appendChild(canvas);
        host.appendChild(panel);

        const topPanel = el("section", { class: "panel" },
          el("div", { class: "subpanel-title" }, "Top symbols"),
          topTable);
        host.appendChild(topPanel);

        renderModeBar();
        renderThreadBar();
        renderBreadcrumb([]);
      },
      async refresh() { await refresh(); },
    };
    function renderModeBar() {
      modeBar.innerHTML = "";
      const modes = ["on-cpu","off-cpu","off-cpu-locks","pagefault",
                     "wakeup","blockflame","malloc"];
      for (const m of modes) {
        const b = el("button",
          { class: "btn" + (ps.mode === m ? " active" : "") }, m);
        b.addEventListener("click", () => {
          ps.mode = m;
          renderModeBar();
          refresh();
        });
        modeBar.appendChild(b);
      }
    }
    function renderThreadBar() {
      threadBar.innerHTML = "";
      for (const t of ["all", "request", "background"]) {
        const b = el("button",
          { class: "btn" + (ps.thread === t ? " active" : "") }, t);
        b.addEventListener("click", () => {
          ps.thread = t;
          renderThreadBar();
          refresh();
        });
        threadBar.appendChild(b);
      }
    }
    function renderBreadcrumb(path) {
      breadcrumbEl.innerHTML = "";
      const root = el("a",
        { class: "crumb", href: "#",
          events: { click: e => { e.preventDefault(); flame.zoomTo([]); } } },
        "(root)");
      breadcrumbEl.appendChild(root);
      path.forEach((seg, i) => {
        breadcrumbEl.appendChild(el("span", { class: "sep" }, " ▸ "));
        const c = el("a",
          { class: "crumb", href: "#",
            events: { click: e => {
              e.preventDefault();
              flame.zoomTo(path.slice(0, i + 1));
            } } }, seg);
        breadcrumbEl.appendChild(c);
      });
    }
    async function refresh() {
      const [status, pids] = await Promise.all([
        getJSON("/api/flame/status"),
        getJSON("/api/proc/pids"),
      ]);
      const pidList = pids.pids || [];
      const opts = pidList.map(p =>
        `<option value="${p.pid}">${p.pid} (${p.role})</option>`).join("");
      if (pidSel.innerHTML !== opts) pidSel.innerHTML = opts;
      if (ps.pid && pidList.some(p => p.pid === ps.pid))
        pidSel.value = String(ps.pid);
      else if (pids.default) {
        pidSel.value = String(pids.default);
        ps.pid = pids.default;
      }
      const totalSamples = (status.modes || []).reduce(
        (a, m) => a + m.samples, 0);
      statusEl.querySelector(".panel-title").textContent =
        `Flame — ${totalSamples} sampled stacks`
        + (status.perf_status ? ` (${status.perf_status})` : "");

      const args = {
        mode: ps.mode,
        smartmet_only: ps.smartmet_only ? 1 : 0,
        thread: ps.thread,
      };
      if (ps.pid) args.pid = ps.pid;
      const [tree, top] = await Promise.all([
        getJSON("/api/flame/tree", args),
        getJSON("/api/flame/top", args),
      ]);
      flame.setData(tree.stacks || [], { unit: top.unit });
      if (ps.search) flame.setSearch(ps.search);

      renderTable(topTbody, [
        { key: "pct", class: "num", fmt: v => v.toFixed(1) + "%" },
        { key: "symbol",
          html: v => `<span title="${escHtml(v)}">${escHtml(v)}</span>` },
        { key: "weight", class: "num",
          fmt: v => top.unit === "bytes" ? fmtBytes(v)
                    : top.unit === "microseconds" ? (v / 1000).toFixed(1) + "ms"
                    : fmtCount(v) },
      ], top.rows || []);
    }
  })();

  // -- Logs ---------------------------------------------------------

  PANELS.logs = (function () {
    const ps = state.panels.logs = { filter: "", autoscroll: true };
    let scroller, filterInput;
    return {
      title: "Logs",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Logs"),
          el("label", null, "filter",
            (filterInput = textInput("logs-filter", ps.filter, "substring",
              v => { ps.filter = v; refresh(); }))),
          toggleButton("autoscroll", ps.autoscroll,
                        v => { ps.autoscroll = v; }),
        );
        scroller = el("div", { id: "logs-scroll" });
        panel.appendChild(ctrls);
        panel.appendChild(scroller);
        host.appendChild(panel);
      },
      async refresh() { await refresh(); },
    };
    async function refresh() {
      const d = await getJSON("/api/logs",
        { n: 500, filter: ps.filter });
      const html = (d.lines || []).map(line => {
        const lc = line.toLowerCase();
        let cls = "ln";
        if (lc.includes(" error") || lc.includes('" 5')) cls += " err";
        else if (lc.includes(" warn") || lc.includes('" 4')) cls += " warn";
        return `<div class="${cls}">${escHtml(line)}</div>`;
      }).join("");
      scroller.innerHTML = html;
      if (ps.autoscroll) scroller.scrollTop = scroller.scrollHeight;
    }
  })();

  // =================================================================
  // boot, tab strip, polling
  // =================================================================

  function activatePanel(id) {
    if (state.active === id) return;
    state.active = id;
    location.hash = "#/" + id;
    document.querySelectorAll("#tabs .tab").forEach(t =>
      t.classList.toggle("active", t.dataset.id === id));
    const host = document.getElementById("panel-host");
    // Guard init: if a referenced static asset 404s (the symptom is a
    // missing constructor on `window`, e.g. FlameView), the polling
    // loop must NOT keep ticking and re-throwing the same error every
    // 2 s. Drop the panel as inactive and surface the cause.
    try {
      PANELS[id].init(host);
    } catch (e) {
      state.active = null;
      host.innerHTML =
        `<div class="panel panel-empty">panel <strong>${escHtml(id)}</strong> ` +
        `failed to load: ${escHtml(e.message || String(e))}<br>` +
        `<span class="muted">Try a hard refresh (Ctrl-Shift-R) — ` +
        `a new install may have replaced cached static assets.</span></div>`;
      showError(e);
      return;
    }
    PANELS[id].refresh().catch(showError);
  }

  function tickActive() {
    if (!state.active) return;
    PANELS[state.active].refresh()
      .then(() => setIndicator("live", "live"))
      .catch(e => showError(e));
    if (state.modalRefresh) state.modalRefresh().catch(showError);
  }

  function showError(e) {
    setIndicator("error: " + (e.message || e), "error");
    console.error(e);
  }

  async function boot() {
    setIndicator("…");
    let panels;
    try {
      panels = (await getJSON("/api/panels")).panels;
    } catch (e) { showError(e); return; }

    const tabHost = document.getElementById("tabs");
    tabHost.innerHTML = "";
    for (const p of panels) {
      const b = el("button",
        { class: "tab", "data-id": p.id }, p.title);
      b.addEventListener("click", () => activatePanel(p.id));
      tabHost.appendChild(b);
    }

    fetch("/api/health", { cache: "no-store" })
      .then(r => r.json())
      .then(h => {
        document.getElementById("status").textContent =
          h.admin_hosts.length
            ? `hosts: ${h.admin_hosts.join(", ")}`
            : "no admin hosts configured";
      })
      .catch(() => {});

    // Pick initial panel from location hash, fall back to URLs.
    const hash = (location.hash || "").replace(/^#\//, "");
    activatePanel(panels.some(p => p.id === hash) ? hash : "urls");

    state.poll = setInterval(tickActive, REFRESH_MS);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
