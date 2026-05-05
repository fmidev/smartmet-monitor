// app.js — smartmet-webmon front-end. Multi-panel tab UI; one tab per
// curses panel from smtop. Vanilla JS, no build step. Polls the active
// panel's endpoints every REFRESH_MS.

(function () {
  "use strict";

  const REFRESH_MS = 2000;

  const state = {
    active: null,              // current panel id
    activeCluster: null,       // current cluster name (null = single-host)
    clusters: [],              // list of configured clusters (from /api/clusters)
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

  // Build legend items for cluster multi-line charts. Combines the
  // chart's actual series (successful backends) with the cc.errors
  // map (failed-fetch backends) — failed backends are omitted from
  // the chart so a flat-zero line doesn't lie about traffic, but
  // they appear in the legend with a ⚠ marker and tooltip so the
  // operator sees the failure. ``hidden`` is the per-panel Set of
  // user-toggled-off labels; ``onToggle(label)`` updates it and
  // triggers a redraw.
  function _buildClusterLegend(cc, series, hidden, onToggle) {
    const items = [];
    const labelsInChart = new Set(series.map(s => s.label));
    for (const s of series) {
      const errMsg = cc.errors && cc.errors[s.label];
      const item = el("span",
        { class: "lg-item"
                 + (hidden.has(s.label) ? " disabled" : "")
                 + (errMsg ? " error" : ""),
          title: errMsg || "" },
        el("span", { class: "lg-swatch",
                      style: `background:${s.color}` }),
        s.label + (errMsg ? " ⚠" : ""));
      item.addEventListener("click", () => onToggle(s.label));
      items.push(item);
    }
    // Errored backends — sorted, color-hashed for consistency, but
    // not toggleable (clicking a not-in-chart legend entry would do
    // nothing visible).
    const erroredLabels = Object.keys(cc.errors || {})
      .filter(l => !labelsInChart.has(l)).sort();
    for (const label of erroredLabels) {
      const errMsg = cc.errors[label];
      const item = el("span",
        { class: "lg-item error disabled", title: errMsg || "" },
        el("span", { class: "lg-swatch",
                      style: `background:${smChart.colorFor(label)};opacity:0.4` }),
        label + " ⚠");
      items.push(item);
    }
    return items;
  }

  // ---- Card collapse (click-to-toggle, persisted) ----------------
  //
  // Every ``.section-card`` in every panel can be collapsed by clicking
  // its ``<h4>`` title. The card body hides via a CSS sibling selector
  // (no DOM-wrapping required), the chevron rotates 90°, and the state
  // persists in localStorage keyed by panel-id + slugified card title
  // so reloading the dashboard or switching panels keeps the operator's
  // layout. Cards in panels that rebuild their DOM on every refresh
  // (Network, Proc, modal-detail) re-acquire the collapsed class on
  // each setupCardCollapse() call — the persistence layer handles the
  // re-decoration.
  //
  // Per-card vertical resize is deliberately deferred for now: native
  // CSS ``resize: vertical`` works, but it fights with the canvas
  // redraw cycle in panels that rebuild HTML on each refresh. A
  // ResizeObserver + targeted-canvas-redraw approach is the natural
  // next step but earns its complexity only once operators show they
  // need height control beyond the existing per-canvas defaults.

  const _CARD_STATE_KEY = "smwebmon:cardState:v1";

  function _loadCardState() {
    try { return JSON.parse(localStorage.getItem(_CARD_STATE_KEY) || "{}"); }
    catch (e) { return {}; }
  }
  function _saveCardState(s) {
    try { localStorage.setItem(_CARD_STATE_KEY, JSON.stringify(s)); }
    catch (e) { /* private mode / quota — silently skip */ }
  }
  function _slugify(s) {
    return String(s || "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  }

  function setupCardCollapse(host) {
    if (!state.active) return;
    const panelId = state.active;
    const allState = _loadCardState();
    const panelState = allState[panelId] || {};
    const titleByCardId = {};        // cardId -> human-readable title
    for (const card of host.querySelectorAll(".section-card")) {
      // Two card-header shapes in this codebase:
      //   1. <h4>title</h4> ... (Network / Proc / Overview history)
      //   2. <div class="panel-controls"><span class="panel-title">
      //        title</span><label>...controls...</label></div> ...
      //      (cluster-mode trend cards in Caches / Services / Plugins / Keys)
      const titleEl = card.querySelector("h4, .panel-title");
      if (!titleEl) continue;
      const titleText = (titleEl.textContent || titleEl.innerText || "").trim();
      const cardId = card.dataset.cardId || _slugify(titleText);
      if (!cardId) continue;
      card.dataset.cardId = cardId;
      titleByCardId[cardId] = titleText;

      if (panelState[cardId] && panelState[cardId].collapsed) {
        card.classList.add("collapsed");
      } else {
        card.classList.remove("collapsed");
      }

      // Wire the click handler once per title element. Re-rendered
      // headers (DOM rebuild) get a fresh element so the flag is
      // naturally undefined and we re-wire.
      if (!titleEl._collapseWired) {
        titleEl._collapseWired = true;
        titleEl.addEventListener("click", (e) => {
          // Don't toggle when the click landed on an input/select/
          // button inside the title row (cluster trend cards have a
          // dropdown picker in the same row as the title).
          if (e.target !== titleEl
              && e.target.closest("input, select, button, label")) {
            return;
          }
          card.classList.toggle("collapsed");
          const cs = _loadCardState();
          const ps = cs[panelId] || (cs[panelId] = {});
          if (card.classList.contains("collapsed")) {
            ps[cardId] = Object.assign({}, ps[cardId] || {}, {
              collapsed: true, title: titleText,
            });
          } else if (ps[cardId]) {
            delete ps[cardId].collapsed;
            if (!Object.keys(ps[cardId]).length) delete ps[cardId];
          }
          if (!Object.keys(cs[panelId] || {}).length) delete cs[panelId];
          _saveCardState(cs);
          // Re-build the strip to reflect the change.
          _renderHiddenStrip(host, panelId, _loadCardState()[panelId] || {},
                             titleByCardId);
        });
      }
    }
    _renderHiddenStrip(host, panelId, panelState, titleByCardId);
  }

  // Render (or clear) the per-panel hidden-cards strip at the top of
  // the panel host. Empty when nothing is collapsed — the CSS hides
  // empty strips so no chrome is taken in the common case. Each chip
  // restores its card on click via the same localStorage round-trip
  // setupCardCollapse uses, then re-runs setupCardCollapse to apply.
  function _renderHiddenStrip(host, panelId, panelState, titleByCardId) {
    const hiddenIds = Object.keys(panelState || {})
      .filter(id => panelState[id] && panelState[id].collapsed);
    let strip = host.querySelector(":scope > .hidden-strip");
    if (!hiddenIds.length) {
      if (strip) strip.replaceChildren();
      return;
    }
    if (!strip) {
      strip = document.createElement("div");
      strip.className = "hidden-strip";
      host.insertBefore(strip, host.firstChild);
    }
    const items = [el("span", { class: "hs-label" }, "hidden:")];
    for (const id of hiddenIds.sort()) {
      // Title comes from the live DOM if the card still exists in the
      // tree (display:none does not strip it), else from the saved
      // ``title`` field stored when it was collapsed.
      const label = (titleByCardId && titleByCardId[id])
                  || (panelState[id] && panelState[id].title)
                  || id;
      const chip = el("button", { class: "hidden-chip", type: "button" },
                       label);
      chip.addEventListener("click", () => {
        const cs = _loadCardState();
        const ps = cs[panelId] || {};
        if (ps[id]) {
          delete ps[id].collapsed;
          delete ps[id].title;
          if (!Object.keys(ps[id]).length) delete ps[id];
        }
        if (!Object.keys(ps).length) delete cs[panelId];
        else cs[panelId] = ps;
        _saveCardState(cs);
        // Re-run on the current host: the card unhides and the strip
        // refreshes minus this chip.
        setupCardCollapse(host);
      });
      items.push(chip);
    }
    strip.replaceChildren(...items);
  }

  // ---- HTTP -------------------------------------------------------

  async function getJSON(path, params) {
    // Auto-attach the active cluster to every request so panels never
    // have to know about clustering. Endpoints that don't care about
    // ?cluster= ignore it; endpoints that do (every store-bound /api/*)
    // resolve to the right cluster's store.
    const merged = Object.assign({}, params || {});
    if (state.activeCluster && !("cluster" in merged)) {
      merged.cluster = state.activeCluster;
    }
    const qs = Object.keys(merged).length
      ? "?" + new URLSearchParams(merged).toString() : "";
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
      ps.detailHidden = ps.detailHidden || new Set();
      modal.open(url);
      const body = modal.body();
      // Cluster mode: swap chart heading + add a metric picker and a
      // legend container alongside the canvas. The store-derived
      // single-line "/api/urls/chart" lives in the cluster's per-cluster
      // store too (since lastrequests still feeds it), but only as an
      // aggregate — the per-backend overlay needs the dedicated
      // endpoint.
      const isCluster = !!state.activeCluster;
      const chartHeading = isCluster
        ? `<div class="panel-controls">
             <h3 style="margin:0;flex:1">Per-backend latency, last 60 min</h3>
             <label>metric
               <select id="d-metric">
                 <option value="p95_ms">p95</option>
                 <option value="p50_ms">p50</option>
                 <option value="mean_ms">mean</option>
                 <option value="max_ms">max</option>
                 <option value="count">count</option>
               </select>
             </label>
           </div>`
        : `<h3>Mean latency, last 60 min</h3>`;
      body.innerHTML = `
        <h3>Windowed stats</h3>
        <table class="data-table"><thead><tr>
          <th>window</th><th class="num">reqs</th><th class="num">mean</th>
          <th class="num">p50</th><th class="num">p95</th>
          <th class="num">p99</th><th class="num">max</th>
          <th class="num">avg sz</th><th class="num">total</th>
          <th class="num">err%</th>
        </tr></thead><tbody></tbody></table>
        ${chartHeading}
        <canvas class="chart" data-role="line" height="160"></canvas>
        <div class="chart-legend" id="d-legend"></div>
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
      ps.detailMetric = ps.detailMetric || "p95_ms";
      const metricSel = body.querySelector("#d-metric");
      if (metricSel) {
        metricSel.value = ps.detailMetric;
        metricSel.addEventListener("change", () => {
          ps.detailMetric = metricSel.value;
          if (state.modalRefresh) state.modalRefresh().catch(() => {});
        });
      }
      const refreshDetail = async () => {
        // Only /api/urls/detail uses the per-cluster store path.
        // The chart switches between the multi-line cluster endpoint
        // and the single-line store endpoint based on cluster mode.
        const chartReq = isCluster
          ? getJSON("/api/cluster/urls/chart",
                    { url, minutes: 60, metric: ps.detailMetric })
          : getJSON("/api/urls/chart",  { url, window: 60 });
        const [d, c] = await Promise.all([
          getJSON("/api/urls/detail", { url, window: ps.window }),
          chartReq,
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
        const legend = body.querySelector("#d-legend");
        if (isCluster && Array.isArray(c.series)) {
          const series = c.series.map(s => Object.assign({}, s, {
            color: smChart.colorFor(s.label),
          }));
          const visible = series.filter(s => !ps.detailHidden.has(s.label));
          smChart.drawLineMulti(cs[0], visible,
            { last_ts: c.last_ts, step_seconds: c.step_seconds,
              fmtY: ps.detailMetric === "count"
                    ? v => Math.round(v) : fmtMs });
          // Legend = successful series + any errored prefixes (which
          // the server omits from `series` so the chart doesn't show
          // a misleading flat-zero line for a failed backend).
          legend.replaceChildren(..._buildClusterLegend(c, series,
            ps.detailHidden,
            label => {
              if (ps.detailHidden.has(label)) ps.detailHidden.delete(label);
              else ps.detailHidden.add(label);
              if (state.modalRefresh) state.modalRefresh().catch(() => {});
            }));
        } else {
          legend.replaceChildren();
          smChart.drawLine(cs[0], (c.values || []).map(Number),
                            { xLabels: ["-60m", "now"],
                              last_ts: c.last_ts,
                              step_seconds: c.step_seconds });
        }
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
      // Cluster-mode chart state.
      cPlugin: "", cMetric: "p95_ms", cMinutes: 60, cHidden: new Set(),
    };
    let tbody, chartCard, chartCanvas, legendEl, namePicker, metricPicker;
    const C_METRICS = [
      ["p95_ms",  "p95"],
      ["p50_ms",  "p50"],
      ["mean_ms", "mean"],
      ["max_ms",  "max"],
      ["count",   "count"],
    ];
    return {
      title: "Plugins",
      init(host) {
        host.replaceChildren();
        // Cluster-mode chart card on top. Hidden in single-host mode.
        chartCard = el("div", { class: "section-card", id: "plugins-chart-card" });
        const cCtrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Per-backend plugin trend"),
          el("label", null, "plugin",
            (namePicker = el("select", { id: "plugins-name" }))),
          el("label", null, "metric",
            (metricPicker = el("select", { id: "plugins-metric" },
              ...C_METRICS.map(([v, l]) => {
                const o = el("option", { value: v }, l);
                if (v === ps.cMetric) o.setAttribute("selected", "");
                return o;
              })))),
        );
        chartCard.appendChild(cCtrls);
        chartCanvas = el("canvas", { class: "chart", height: "180" });
        chartCard.appendChild(chartCanvas);
        legendEl = el("div", { class: "chart-legend" });
        chartCard.appendChild(legendEl);
        chartCard.style.display = "none";
        host.appendChild(chartCard);

        namePicker.addEventListener("change", () => {
          ps.cPlugin = namePicker.value;
          PANELS.plugins.refresh().catch(() => {});
        });
        metricPicker.addEventListener("change", () => {
          ps.cMetric = metricPicker.value;
          PANELS.plugins.refresh().catch(() => {});
        });

        const panel = el("section", { class: "panel" });
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Plugins"),
          el("label", null, "window",
            selectInput("p-window", ps.window,
              [["60s","60s"],["5m","5m"],["15m","15m"],["60m","60m"]]
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
      const isCluster = !!state.activeCluster;
      chartCard.style.display = isCluster ? "" : "none";
      if (isCluster) await refreshClusterChart();
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
          // Pull last_ts / step_seconds from the trends payload so the
          // sparkline tooltip can show the time at the cursor (without
          // them the tooltip would display value-only — no time).
          const ltOpts = trends.last_ts != null && trends.step_seconds
            ? { last_ts: trends.last_ts, step_seconds: trends.step_seconds }
            : {};
          requestAnimationFrame(() => {
            smChart.drawSparkline(lat, m.mean_ms || [],
                                  { color: smChart.PALETTE.line,
                                    fmtY: fmtMs, ...ltOpts });
            smChart.drawSparkline(sz, m.bytes_mean || [],
                                  { color: smChart.PALETTE.accent2,
                                    fmtY: fmtBytes, ...ltOpts });
          });
        },
      });
    }
    async function refreshClusterChart() {
      // First call may have no plugin chosen — endpoint then returns
      // the plugin_names list so we can populate the picker. Once the
      // picker has a selection we issue a second call to fetch the
      // chart for that plugin. (Could be folded into one call with
      // a default-pick rule on the server, but keeping the two-step
      // shape mirrors the URLs panel and keeps the picker logic
      // server-driven.)
      let cc = await getJSON("/api/cluster/plugins/chart", {
        plugin: ps.cPlugin, metric: ps.cMetric, minutes: ps.cMinutes,
      });
      const names = cc.plugin_names || [];
      if (!ps.cPlugin && names.length) ps.cPlugin = names[0];
      const current = ps.cPlugin;
      namePicker.replaceChildren(...names.map(n => {
        const o = el("option", { value: n }, n);
        if (n === current) o.setAttribute("selected", "");
        return o;
      }));
      if (current && namePicker.value !== current) {
        namePicker.value = current;
      }
      if (current && cc.plugin !== current) {
        cc = await getJSON("/api/cluster/plugins/chart", {
          plugin: current, metric: ps.cMetric, minutes: ps.cMinutes,
        });
      }
      const series = (cc.series || []).map(s => Object.assign({}, s, {
        color: smChart.colorFor(s.label),
      }));
      const visible = series.filter(s => !ps.cHidden.has(s.label));
      const fmtY = ps.cMetric === "count" ? v => Math.round(v) : fmtMs;
      smChart.drawLineMulti(chartCanvas, visible,
        { fmtY, last_ts: cc.last_ts, step_seconds: cc.step_seconds });
      legendEl.replaceChildren(..._buildClusterLegend(cc, series,
        ps.cHidden,
        label => {
          if (ps.cHidden.has(label)) ps.cHidden.delete(label);
          else ps.cHidden.add(label);
          PANELS.plugins.refresh().catch(() => {});
        }));
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
        const isCluster = !!state.activeCluster;
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

        if (isCluster) {
          // One parallel fetch produces all five mini-chart series
          // — N backend HTTP calls total, not 5N.
          const metrics = Object.keys(charts);
          const cc = await getJSON("/api/cluster/overview/chart", {
            metrics: metrics.join(","), minutes: 60,
          });
          // The bytes and err_pct metrics aren't carried in the row
          // duration field, so cluster_overview_chart returns 0 for
          // them — fall back to drawLine on those with global_series
          // values from the per-cluster store. Acceptable since
          // bytes/err_pct global aggregates are still meaningful.
          for (const m of metrics) {
            const fmtY = (m === "bytes") ? fmtBytes
                        : (m === "count" || m === "err_pct")
                          ? v => Math.round(v) : fmtMs;
            const onePerHost = cc.charts && cc.charts[m];
            if (m === "bytes" || m === "err_pct" || !onePerHost
                || !onePerHost.series || !onePerHost.series.length) {
              const c = await getJSON("/api/overview/chart", { metric: m });
              smChart.drawLine(charts[m], (c.values || []).map(Number),
                                { fmtY,
                                  last_ts: c.last_ts,
                                  step_seconds: c.step_seconds });
            } else {
              const series = onePerHost.series.map(s =>
                Object.assign({}, s, { color: smChart.colorFor(s.label) }));
              smChart.drawLineMulti(charts[m], series,
                { fmtY, last_ts: onePerHost.last_ts,
                  step_seconds: onePerHost.step_seconds });
            }
          }
        } else {
          for (const m of Object.keys(charts)) {
            const c = await getJSON("/api/overview/chart", { metric: m });
            const fmtY = (m === "bytes") ? fmtBytes
                        : (m === "count" || m === "err_pct")
                          ? v => Math.round(v) : fmtMs;
            smChart.drawLine(charts[m], (c.values || []).map(Number),
                              { fmtY,
                                last_ts: c.last_ts,
                                step_seconds: c.step_seconds });
          }
        }
      },
    };
  })();

  // -- Caches -------------------------------------------------------

  PANELS.caches = (function () {
    const ps = state.panels.caches = {
      cacheName: "", metric: "hits_per_min", hidden: new Set(),
    };
    let tbody, chartCard, chartCanvas, legendEl, namePicker, metricPicker;
    const METRICS = [
      ["hits_per_min",    "hits/min"],
      ["inserts_per_min", "inserts/min"],
      ["hitrate",         "hit %"],
      ["size",            "size"],
    ];
    return {
      title: "Caches",
      init(host) {
        host.replaceChildren();
        // Cluster-mode chart card. Hidden by default; shown when a
        // cluster is selected. Single-host mode keeps using the
        // per-row sparkline and ignores this card.
        chartCard = el("div", { class: "section-card", id: "caches-chart-card" });
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Per-backend cache trend"),
          el("label", null, "cache",
            (namePicker = el("select", { id: "caches-name" }))),
          el("label", null, "metric",
            (metricPicker = el("select", { id: "caches-metric" },
              ...METRICS.map(([v, l]) => {
                const o = el("option", { value: v }, l);
                if (v === ps.metric) o.setAttribute("selected", "");
                return o;
              })))),
        );
        chartCard.appendChild(ctrls);
        chartCanvas = el("canvas", { class: "chart", height: "180" });
        chartCard.appendChild(chartCanvas);
        legendEl = el("div", { class: "chart-legend" });
        chartCard.appendChild(legendEl);
        chartCard.style.display = "none";
        host.appendChild(chartCard);

        namePicker.addEventListener("change", () => {
          ps.cacheName = namePicker.value;
          PANELS.caches.refresh().catch(() => {});
        });
        metricPicker.addEventListener("change", () => {
          ps.metric = metricPicker.value;
          PANELS.caches.refresh().catch(() => {});
        });

        const panel = el("section", { class: "panel" },
          el("div", { class: "panel-controls" },
            el("span", { class: "panel-title" }, "Caches")),
          el("table", { class: "data-table" },
            el("thead", null, el("tr", null,
              ...["host","cache","size","max","hits/m","ins/m","hit%",
                  "fill","hits trend"]
                .map((h,i) => el("th",
                  { class: i === 0 ? "" :
                            i === 1 ? "name-col" :
                            (i === 7 || i === 8) ? "spark" : "num" }, h)))),
            el("tbody")));
        tbody = panel.querySelector("tbody");
        host.appendChild(panel);
      },
      async refresh() {
        const isCluster = !!state.activeCluster;
        chartCard.style.display = isCluster ? "" : "none";
        if (isCluster) {
          const cc = await getJSON("/api/caches/cluster_chart", {
            cache_name: ps.cacheName, metric: ps.metric,
          });
          // Refresh cache picker options from server-discovered names.
          const names = cc.cache_names || [];
          if (!ps.cacheName && names.length) ps.cacheName = names[0];
          const current = ps.cacheName;
          namePicker.replaceChildren(
            ...names.map(n => {
              const o = el("option", { value: n }, n);
              if (n === current) o.setAttribute("selected", "");
              return o;
            }));
          if (current && namePicker.value !== current) {
            namePicker.value = current;
          }
          // If picker changed (was empty), refetch chart for the new
          // cache name on next tick. Otherwise render now.
          if (current && cc.cache_name !== current) {
            const cc2 = await getJSON("/api/caches/cluster_chart", {
              cache_name: current, metric: ps.metric,
            });
            _renderCachesChart(cc2);
          } else {
            _renderCachesChart(cc);
          }
        }
        const [data, trends] = await Promise.all([
          getJSON("/api/caches"),
          getJSON("/api/caches/trends"),
        ]);
        const tBy = new Map((trends.rows || [])
          .map(r => [`${r.host}::${r.cache_name}`, r.values]));
        renderTable(tbody, [
          { key: "host" },
          { key: "cache_name", class: "name-col",
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
            // step_seconds + last_ts come from the /caches/trends
            // response — same plumbing the cluster chart already uses,
            // here threaded into the per-row spark so the tooltip
            // shows the time at the cursor, not just the value.
            const tsOpts = trends.last_ts != null && trends.step_seconds
              ? { last_ts: trends.last_ts, step_seconds: trends.step_seconds }
              : {};
            requestAnimationFrame(() => smChart.drawSparkline(c, series,
              { fmtY: v => v.toFixed(1), ...tsOpts }));
          },
        });
      },
    };
    function _renderCachesChart(cc) {
      const series = (cc.series || []).map(s => Object.assign({}, s, {
        color: smChart.colorFor(s.label),
      }));
      const visible = series.filter(s => !ps.hidden.has(s.label));
      const fmtY = ps.metric === "hitrate"
                   ? v => v.toFixed(0) + "%"
                   : ps.metric === "size"
                     ? fmtCount
                     : v => v.toFixed(1);
      smChart.drawLineMulti(chartCanvas, visible,
        { fmtY, last_ts: cc.last_ts, step_seconds: cc.step_seconds });
      legendEl.replaceChildren(...series.map(s => {
        const item = el("span",
          { class: "lg-item" + (ps.hidden.has(s.label) ? " disabled" : "") },
          el("span", { class: "lg-swatch",
                        style: `background:${s.color}` }),
          s.label);
        item.addEventListener("click", () => {
          if (ps.hidden.has(s.label)) ps.hidden.delete(s.label);
          else ps.hidden.add(s.label);
          PANELS.caches.refresh().catch(() => {});
        });
        return item;
      }));
    }
  })();

  // -- Services -----------------------------------------------------

  PANELS.services = (function () {
    const ps = state.panels.services = {
      handler: "", metric: "req_per_min", hidden: new Set(),
    };
    let tbody, chartCard, chartCanvas, legendEl, namePicker, metricPicker;
    const METRICS = [
      ["req_per_min",  "req/min"],
      ["req_per_hour", "req/hour"],
      ["req_per_day",  "req/day"],
      ["avg_ms",       "avg ms"],
      ["avg_cpu_ms",   "avg cpu ms"],
    ];
    return {
      title: "Services",
      init(host) {
        host.replaceChildren();
        chartCard = el("div", { class: "section-card", id: "services-chart-card" });
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Per-backend handler trend"),
          el("label", null, "handler",
            (namePicker = el("select", { id: "services-name" }))),
          el("label", null, "metric",
            (metricPicker = el("select", { id: "services-metric" },
              ...METRICS.map(([v, l]) => {
                const o = el("option", { value: v }, l);
                if (v === ps.metric) o.setAttribute("selected", "");
                return o;
              })))),
        );
        chartCard.appendChild(ctrls);
        chartCanvas = el("canvas", { class: "chart", height: "180" });
        chartCard.appendChild(chartCanvas);
        legendEl = el("div", { class: "chart-legend" });
        chartCard.appendChild(legendEl);
        chartCard.style.display = "none";
        host.appendChild(chartCard);

        namePicker.addEventListener("change", () => {
          ps.handler = namePicker.value;
          PANELS.services.refresh().catch(() => {});
        });
        metricPicker.addEventListener("change", () => {
          ps.metric = metricPicker.value;
          PANELS.services.refresh().catch(() => {});
        });

        const panel = el("section", { class: "panel" },
          el("div", { class: "panel-controls" },
            el("span", { class: "panel-title" }, "Services")),
          el("table", { class: "data-table" },
            el("thead", null, el("tr", null,
              ...["host","handler","req/min","req/h","req/d","avg","cpu%","trend"]
                .map((h,i) => el("th",
                  { class: i === 0 ? "" :
                            i === 1 ? "name-col" :
                            (i === 7) ? "spark" : "num" }, h)))),
            el("tbody")));
        tbody = panel.querySelector("tbody");
        host.appendChild(panel);
      },
      async refresh() {
        const isCluster = !!state.activeCluster;
        chartCard.style.display = isCluster ? "" : "none";
        if (isCluster) {
          const cc = await getJSON("/api/services/cluster_chart", {
            handler: ps.handler, metric: ps.metric,
          });
          const handlers = cc.handlers || [];
          if (!ps.handler && handlers.length) ps.handler = handlers[0];
          const current = ps.handler;
          namePicker.replaceChildren(
            ...handlers.map(n => {
              const o = el("option", { value: n }, n);
              if (n === current) o.setAttribute("selected", "");
              return o;
            }));
          if (current && namePicker.value !== current) {
            namePicker.value = current;
          }
          if (current && cc.handler !== current) {
            const cc2 = await getJSON("/api/services/cluster_chart", {
              handler: current, metric: ps.metric,
            });
            _renderServicesChart(cc2);
          } else {
            _renderServicesChart(cc);
          }
        }
        const [data, trends] = await Promise.all([
          getJSON("/api/services"),
          getJSON("/api/services/trends"),
        ]);
        const tBy = new Map((trends.rows || [])
          .map(r => [`${r.host}::${r.handler}`, r.values]));
        renderTable(tbody, [
          { key: "host" },
          { key: "handler", class: "name-col",
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
            // step_seconds + last_ts from /services/trends so the spark
            // tooltip shows time-at-cursor, not just the value.
            const tsOpts = trends.last_ts != null && trends.step_seconds
              ? { last_ts: trends.last_ts, step_seconds: trends.step_seconds }
              : {};
            requestAnimationFrame(() => smChart.drawSparkline(c, series,
              { fmtY: v => v.toFixed(0), ...tsOpts }));
          },
        });
      },
    };
    function _renderServicesChart(cc) {
      const series = (cc.series || []).map(s => Object.assign({}, s, {
        color: smChart.colorFor(s.label),
      }));
      const visible = series.filter(s => !ps.hidden.has(s.label));
      const fmtY = (ps.metric === "avg_ms" || ps.metric === "avg_cpu_ms")
                   ? fmtMs : v => v.toFixed(0);
      smChart.drawLineMulti(chartCanvas, visible,
        { fmtY, last_ts: cc.last_ts, step_seconds: cc.step_seconds });
      legendEl.replaceChildren(...series.map(s => {
        const item = el("span",
          { class: "lg-item" + (ps.hidden.has(s.label) ? " disabled" : "") },
          el("span", { class: "lg-swatch",
                        style: `background:${s.color}` }),
          s.label);
        item.addEventListener("click", () => {
          if (ps.hidden.has(s.label)) ps.hidden.delete(s.label);
          else ps.hidden.add(s.label);
          PANELS.services.refresh().catch(() => {});
        });
        return item;
      }));
    }
  })();

  // -- Active -------------------------------------------------------

  PANELS.active = (function () {
    const ps = state.panels.active = { hidden: new Set() };
    let chartCanvas, tbody, headEl, legendEl;
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
          el("canvas", { class: "chart", height: "160" }));
        chartCanvas = chartCard.querySelector("canvas");
        legendEl = el("div", { class: "chart-legend" });
        chartCard.appendChild(legendEl);
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
        // In cluster mode, request the per-host series for line
        // overlays. Otherwise fall back to the aggregated single line.
        const isCluster = !!state.activeCluster;
        const [tab, ch] = await Promise.all([
          getJSON("/api/active"),
          getJSON("/api/active/chart",
            isCluster ? { multi: 1 } : null),
        ]);

        if (isCluster && Array.isArray(ch.series)) {
          // Multi-line cluster mode: one line per backend, colored by
          // stable hash, clickable legend.
          const series = ch.series.map(s => Object.assign({}, s, {
            color: smChart.colorFor(s.label),
          }));
          // Apply user's hide-toggles (legend clicks).
          const visible = series.filter(s => !ps.hidden.has(s.label));
          smChart.drawLineMulti(chartCanvas, visible,
            { fmtY: v => Math.round(v),
              last_ts: ch.last_ts,
              step_seconds: ch.step_seconds });
          // Render legend: every backend's swatch + label, with
          // visibility toggled on click.
          legendEl.replaceChildren(...series.map(s => {
            const item = el("span",
              { class: "lg-item" + (ps.hidden.has(s.label) ? " disabled" : "") },
              el("span", { class: "lg-swatch",
                            style: `background:${s.color}` }),
              s.label);
            item.addEventListener("click", () => {
              if (ps.hidden.has(s.label)) ps.hidden.delete(s.label);
              else ps.hidden.add(s.label);
              // Force re-render on next tick (or immediately).
              PANELS.active.refresh().catch(() => {});
            });
            return item;
          }));
          // Summary: total across visible lines, peak across all.
          const lastValues = visible
            .map(s => s.values[s.values.length - 1])
            .filter(v => Number.isFinite(v));
          const total = lastValues.reduce((a, b) => a + b, 0);
          const peakAll = series.reduce((m, s) =>
            Math.max(m, ...(s.values.length ? s.values : [0])), 0);
          document.getElementById("active-summary").textContent =
            `cluster total ${total}, per-backend peak ${peakAll}` +
            (ps.hidden.size ? ` (${ps.hidden.size} hidden)` : "");
        } else {
          // Single-host / single-line mode: existing behavior.
          legendEl.replaceChildren();
          smChart.drawLine(chartCanvas, ch.values || [],
            { fmtY: v => Math.round(v),
              last_ts: ch.last_ts,
              step_seconds: ch.step_seconds });
          document.getElementById("active-summary").textContent =
            `current ${ch.current}, peak ${ch.peak}`;
        }

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
      cKey: "", cMetric: "p95_ms", cMinutes: 60, cHidden: new Set(),
    };
    let tbody, chartCard, chartCanvas, legendEl, namePicker, metricPicker;
    const C_METRICS = [
      ["p95_ms",  "p95"],
      ["p50_ms",  "p50"],
      ["mean_ms", "mean"],
      ["max_ms",  "max"],
      ["count",   "count"],
    ];
    return {
      title: "API Keys",
      init(host) {
        host.replaceChildren();
        chartCard = el("div", { class: "section-card", id: "keys-chart-card" });
        const cCtrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Per-backend apikey trend"),
          el("label", null, "apikey",
            (namePicker = el("select", { id: "keys-name" }))),
          el("label", null, "metric",
            (metricPicker = el("select", { id: "keys-metric" },
              ...C_METRICS.map(([v, l]) => {
                const o = el("option", { value: v }, l);
                if (v === ps.cMetric) o.setAttribute("selected", "");
                return o;
              })))),
        );
        chartCard.appendChild(cCtrls);
        chartCanvas = el("canvas", { class: "chart", height: "180" });
        chartCard.appendChild(chartCanvas);
        legendEl = el("div", { class: "chart-legend" });
        chartCard.appendChild(legendEl);
        chartCard.style.display = "none";
        host.appendChild(chartCard);

        namePicker.addEventListener("change", () => {
          ps.cKey = namePicker.value;
          PANELS.keys.refresh().catch(() => {});
        });
        metricPicker.addEventListener("change", () => {
          ps.cMetric = metricPicker.value;
          PANELS.keys.refresh().catch(() => {});
        });

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
      const isCluster = !!state.activeCluster;
      chartCard.style.display = isCluster ? "" : "none";
      if (isCluster) await refreshClusterChart();
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
    async function refreshClusterChart() {
      let cc = await getJSON("/api/cluster/keys/chart", {
        apikey: ps.cKey, metric: ps.cMetric, minutes: ps.cMinutes,
      });
      const keys = cc.apikeys || [];
      if (!ps.cKey && keys.length) ps.cKey = keys[0];
      const current = ps.cKey;
      namePicker.replaceChildren(...keys.map(n => {
        const o = el("option", { value: n }, n);
        if (n === current) o.setAttribute("selected", "");
        return o;
      }));
      if (current && namePicker.value !== current) {
        namePicker.value = current;
      }
      if (current && cc.apikey !== current) {
        cc = await getJSON("/api/cluster/keys/chart", {
          apikey: current, metric: ps.cMetric, minutes: ps.cMinutes,
        });
      }
      const series = (cc.series || []).map(s => Object.assign({}, s, {
        color: smChart.colorFor(s.label),
      }));
      const visible = series.filter(s => !ps.cHidden.has(s.label));
      const fmtY = ps.cMetric === "count" ? v => Math.round(v) : fmtMs;
      smChart.drawLineMulti(chartCanvas, visible,
        { fmtY, last_ts: cc.last_ts, step_seconds: cc.step_seconds });
      legendEl.replaceChildren(..._buildClusterLegend(cc, series,
        ps.cHidden,
        label => {
          if (ps.cHidden.has(label)) ps.cHidden.delete(label);
          else ps.cHidden.add(label);
          PANELS.keys.refresh().catch(() => {});
        }));
    }
  })();

  // -- Proc ---------------------------------------------------------

  PANELS.proc = (function () {
    const ps = state.panels.proc = {
      pid: null,
      // Per-backend visibility for cluster-mode legend toggles.
      cHidden: new Set(),
    };
    let pidSel, pidLabel, body;
    return {
      title: "Proc",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });
        pidLabel = el("label", null, "pid",
            (pidSel = el("select", { id: "proc-pid" })));
        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Process"),
          pidLabel);
        pidSel.addEventListener("change",
          () => { ps.pid = +pidSel.value; refresh(); });
        body = el("div");
        panel.appendChild(ctrls);
        panel.appendChild(body);
        host.appendChild(panel);
      },
      async refresh() {
        const isCluster = !!state.activeCluster;
        // The PID selector is meaningless in cluster mode (each
        // backend's smwebmon picks its own default smartmetd) — hide
        // it so it doesn't pretend to control anything.
        if (pidLabel) pidLabel.style.display = isCluster ? "none" : "";
        if (isCluster) { await refreshCluster(); return; }

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
        // Proc samples have explicit timestamps in S.ts (one entry
        // per value); drawLine prefers opts.ts over last_ts/step.
        smChart.drawLine(c("rss"),
          (S.vm_rss_kb || []).map(v => v * 1024),
          { fmtY: fmtBytes, ts: S.ts });
        smChart.drawLine(c("io"), S.io_read_bps || [],
          { fmtY: fmtBytes, lineColor: smChart.PALETTE.line, ts: S.ts });
        smChart.drawLine(c("threads"), S.threads || [],
          { fmtY: v => Math.round(v), ts: S.ts });
        smChart.drawLine(c("majflt"), S.majflt_per_s || [],
          { fmtY: v => v.toFixed(2), ts: S.ts });
      },
    };

    // Cluster-mode render path. Fans out via /api/cluster/proc/detail
    // (which itself parallel-fetches each backend's /api/proc/detail
    // — backend smwebmon must be running and reachable). One chart
    // card per metric, each a multi-line overlay with one line per
    // backend, color-hashed the same way as the other cluster panels.
    async function refreshCluster() {
      const cc = await getJSON("/api/cluster/proc/detail");
      if (!cc.configured) {
        body.innerHTML =
          `<div class="panel-empty">
             cluster Proc panel needs <code>webmon-url-pattern</code>
             in <code>/etc/smartmet-webmon/clusters.conf</code> and
             <code>smwebmon</code> running on each backend.
             See README "Cluster mode" for setup.
           </div>`;
        return;
      }
      const backends = cc.backends || {};
      const errors = cc.errors || {};
      const labels = Object.keys(backends).sort();
      if (!labels.length) {
        const errList = Object.keys(errors).sort()
          .map(p => `<li><code>${escHtml(p)}</code>: ${escHtml(errors[p])}</li>`)
          .join("");
        body.innerHTML =
          `<div class="panel-empty">
             no backends with reachable <code>smwebmon</code> yet.
             ${errList ? `<ul>${errList}</ul>` : ""}
           </div>`;
        return;
      }

      // Build the metric series. Each backend's snapshot has its own
      // .series.ts; we use the longest backend's ts as the chart's
      // x-axis reference (slight inaccuracy across hosts whose admin
      // polling drifted by < 2 s, acceptable for trend visibility).
      let refTs = null;
      for (const label of labels) {
        const ts = (backends[label].series || {}).ts || [];
        if (!refTs || ts.length > refTs.length) refTs = ts;
      }

      const buildSeries = (key, transform) => labels.map(label => {
        const arr = (backends[label].series || {})[key] || [];
        return {
          label,
          color: smChart.colorFor(label),
          values: transform ? arr.map(transform) : arr.slice(),
        };
      });

      const memSeries = buildSeries("vm_rss_kb", v => v * 1024);
      const ioReadSeries = buildSeries("io_read_bps");
      const ioWriteSeries = buildSeries("io_write_bps");
      const threadSeries = buildSeries("threads");
      const majfltSeries = buildSeries("majflt_per_s");

      body.innerHTML = `
        <div class="section-grid">
          <div class="section-card" data-card-id="memory">
            <h4>Memory (RSS) — per backend</h4>
            <canvas class="chart" data-role="rss" height="160"></canvas>
            <div class="chart-legend" data-legend="rss"></div>
          </div>
          <div class="section-card" data-card-id="io">
            <h4>I/O read rate — per backend</h4>
            <canvas class="chart" data-role="io-r" height="120"></canvas>
            <div class="chart-legend" data-legend="io-r"></div>
            <h4 style="margin-top:0.6rem">I/O write rate — per backend</h4>
            <canvas class="chart" data-role="io-w" height="120"></canvas>
          </div>
          <div class="section-card" data-card-id="threads">
            <h4>Threads — per backend</h4>
            <canvas class="chart" data-role="threads" height="120"></canvas>
            <div class="chart-legend" data-legend="threads"></div>
          </div>
          <div class="section-card" data-card-id="majflt">
            <h4>Major page-fault rate — per backend</h4>
            <canvas class="chart" data-role="majflt" height="120"></canvas>
            <div class="chart-legend" data-legend="majflt"></div>
          </div>
        </div>
        ${Object.keys(errors).length
          ? `<div class="muted" style="margin:0.4rem 0.75rem">
               unreachable backends:
               ${Object.keys(errors).sort().map(p =>
                  `<code>${escHtml(p)}</code> (${escHtml(errors[p])})`)
                .join(", ")}
             </div>`
          : ""}
      `;

      const drawWith = (selector, series, fmtY, legendKey) => {
        const canvas = body.querySelector(`canvas[data-role="${selector}"]`);
        if (!canvas) return;
        const visible = series.filter(s => !ps.cHidden.has(s.label));
        smChart.drawLineMulti(canvas, visible,
          { fmtY,
            ts: refTs && refTs.length ? refTs.slice(-visible[0]?.values.length) : undefined });
        if (legendKey) {
          const legend = body.querySelector(
            `[data-legend="${legendKey}"]`);
          if (legend) {
            legend.replaceChildren(...series.map(s => {
              const item = el("span",
                { class: "lg-item" + (ps.cHidden.has(s.label) ? " disabled" : "") },
                el("span", { class: "lg-swatch",
                              style: `background:${s.color}` }),
                s.label);
              item.addEventListener("click", () => {
                if (ps.cHidden.has(s.label)) ps.cHidden.delete(s.label);
                else ps.cHidden.add(s.label);
                PANELS.proc.refresh().catch(() => {});
              });
              return item;
            }));
          }
        }
      };

      drawWith("rss", memSeries, fmtBytes, "rss");
      drawWith("io-r", ioReadSeries, fmtBytes, "io-r");
      drawWith("io-w", ioWriteSeries, fmtBytes, null);
      drawWith("threads", threadSeries, v => Math.round(v), "threads");
      drawWith("majflt", majfltSeries, v => v.toFixed(2), "majflt");
    }
  })();

  // -- Heap (allocator stats from spine ?what=mallocstats) -----------

  PANELS.heap = (function () {
    let body;
    function fragClass(pct) {
      if (pct < 15) return "lat-good";
      if (pct < 30) return "lat-warn";
      return "lat-bad";
    }
    return {
      title: "Heap",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" },
          el("div", { class: "panel-controls" },
            el("span", { class: "panel-title" }, "Heap (allocator)")));
        body = el("div");
        panel.appendChild(body);
        host.appendChild(panel);
      },
      async refresh() {
        const d = await getJSON("/api/heap/detail");
        const hosts = d.hosts || [];
        if (hosts.length === 0) {
          body.innerHTML = '<div class="panel-empty">' +
            'No allocator stats yet. Spine ' +
            '<code>?what=mallocstats</code> requires smartmet-library-spine ' +
            '&ge; 26.4.27 on the target smartmetd.</div>';
          return;
        }
        const cards = hosts.map(h => {
          const L = h.latest;
          const errBlock = h.error
            ? `<div class="panel-empty">last error: ${escHtml(h.error)}</div>`
            : "";
          return `
            <div class="section-card">
              <h4>${escHtml(h.host)} — ${escHtml(h.allocator || "unknown")}
                <span class="muted">${escHtml(h.version || "")}</span></h4>
              ${errBlock}
              <div class="kv">
                <span class="k">allocated</span><span class="v">${fmtBytes(L.allocated)}</span>
                <span class="k">active</span><span class="v">${fmtBytes(L.active)}</span>
                <span class="k">resident</span><span class="v">${fmtBytes(L.resident)}</span>
                <span class="k">mapped</span><span class="v">${fmtBytes(L.mapped)}</span>
                <span class="k">retained</span><span class="v">${fmtBytes(L.retained)}</span>
                <span class="k">metadata</span><span class="v">${fmtBytes(L.metadata)}</span>
                <span class="k">arenas</span><span class="v">${L.narenas}</span>
                <span class="k">fragmentation</span>
                <span class="v ${fragClass(L.fragmentation_pct)}">${L.fragmentation_pct.toFixed(1)}%</span>
                <span class="k">resident overhead</span>
                <span class="v">${L.resident_overhead_pct.toFixed(1)}%</span>
              </div>
              <canvas class="chart" data-host="${escHtml(h.host)}" height="100"></canvas>
            </div>
          `;
        });
        body.innerHTML = cards.join("");
        // Draw allocated/active/resident lines per host. Find canvases
        // by iterating to avoid needing CSS.escape on host names that
        // may contain dots (FQDNs always do).
        const canvases = body.querySelectorAll("canvas[data-host]");
        for (const canvas of canvases) {
          const hostName = canvas.getAttribute("data-host");
          const h = hosts.find(x => x.host === hostName);
          if (!h) continue;
          const series = h.series || [];
          const palette = smChart.PALETTE;
          smChart.drawLineMulti(canvas, [
            { label: "allocated", values: series.map(s => s.allocated),
              color: palette.line },
            { label: "active", values: series.map(s => s.active),
              color: palette.accent },
            { label: "resident", values: series.map(s => s.resident),
              color: palette.accent2 },
          ], { fmtY: fmtBytes });
        }
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

        // Now paint canvases. last_ts + step_seconds from the
        // /network/detail response let every chart's tooltip show
        // the time at the cursor — without them the tooltip would
        // be value-only, which is disorienting against a coarse
        // time axis.
        const tsOpts = d.last_ts != null && d.step_seconds
          ? { last_ts: d.last_ts, step_seconds: d.step_seconds }
          : {};
        smChart.drawLine(body.querySelector('canvas[data-tcp="retrans"]'),
                          sum.retrans_per_s || [],
                          { fmtY: v => v.toFixed(1), ...tsOpts });
        for (const s of d.states) {
          const c = body.querySelector(
            `canvas[data-state="${cssEsc(s.state)}"]`);
          if (c) smChart.drawSparkline(c, s.trend || [],
                                        { fmtY: v => Math.round(v),
                                          ...tsOpts });
        }
        for (const f of d.ifaces) {
          const rx = body.querySelector(
            `canvas[data-iface="${cssEsc(f.iface)}"][data-dir="rx"]`);
          const tx = body.querySelector(
            `canvas[data-iface="${cssEsc(f.iface)}"][data-dir="tx"]`);
          if (rx) smChart.drawLine(rx, f.rx_bps || [],
                                    { fmtY: fmtBytes,
                                      lineColor: smChart.PALETTE.good,
                                      ...tsOpts });
          if (tx) smChart.drawLine(tx, f.tx_bps || [],
                                    { fmtY: fmtBytes,
                                      lineColor: smChart.PALETTE.warn,
                                      ...tsOpts });
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
        searchBox, breadcrumbEl, topTbody, errorEl;
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
        errorEl = el("div", { id: "flame-error", class: "panel-empty hidden" });

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
        panel.appendChild(errorEl);
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
    // Surface the multi-line errors the panel header truncates.
    // Triggers when there are no samples at all OR when any sampler
    // is in a "failed" state. The on-CPU sampler's full perf stderr
    // (multi-line, paragraph-shaped) is the most useful payload —
    // shown verbatim in monospace so the operator sees what perf
    // actually said, not the panel-friendly first line.
    function renderFlameDiagnostics(status, totalSamples, mode) {
      const issues = [];
      const isFailing = s => s && /\bfail|error|denied|not found|unavailable|paranoid/i.test(s);
      // Show only the current mode's sampler diagnostics. Showing
      // every sampler's status here put failures from runqlat /
      // biolat / other-mode samplers under the on-CPU panel and so
      // on, which made no sense in the panel's own context. biolat
      // / runqlat / perfstat live in the Proc panel and surface
      // their failures there.
      const modeSampler = {
        "on-cpu":        ["on-CPU (perf record)",     status.perf_status,
                                                       status.perf_last_error],
        "off-cpu":       ["off-CPU (offcputime-bpfcc)", status.offcpu_status,
                                                       status.offcpu_last_error],
        "off-cpu-locks": ["off-CPU locks (offcputime-bpfcc)",
                                                       status.offcpu_status,
                                                       status.offcpu_last_error],
        "pagefault":     ["page-fault",                status.pagefault_status,
                                                       status.pagefault_last_error],
        "wakeup":        ["wakeup",                    status.wakeup_status,
                                                       status.wakeup_last_error],
        "blockflame":    ["block-I/O (blockflame)",    status.blockflame_status,
                                                       status.blockflame_last_error],
        "malloc":        ["malloc",                    status.malloc_status,
                                                       status.malloc_last_error],
      };
      const entry = modeSampler[mode];
      if (entry) {
        const [label, statusText, lastError] = entry;
        if (lastError) {
          issues.push({ label, text: lastError });
        } else if (isFailing(statusText)) {
          issues.push({ label, text: statusText });
        }
      }
      if (totalSamples > 0 && issues.length === 0) {
        errorEl.classList.add("hidden");
        return;
      }
      if (issues.length === 0) {
        errorEl.classList.add("hidden");
        return;
      }
      const blocks = issues.map(i =>
        `<div class="flame-issue">` +
        `<div class="flame-issue-label">${escHtml(i.label)}</div>` +
        `<pre class="flame-issue-text">${escHtml(i.text)}</pre>` +
        `</div>`
      ).join("");
      errorEl.innerHTML =
        `<div class="flame-issues-head">` +
        `<strong>Sampler diagnostics</strong> ` +
        `<span class="muted">(${issues.length} issue${
          issues.length === 1 ? "" : "s"})</span></div>` + blocks;
      errorEl.classList.remove("hidden");
    }

    function renderBreadcrumb(path) {
      breadcrumbEl.innerHTML = "";
      // Visible "Zoom out" affordance for operators who don't try
      // right-click. Disabled when already at root so the control
      // never lies about what it'll do. Right-click on the canvas
      // pops one level; this button pops one level too.
      const upBtn = el("button",
        { class: "btn flame-zoom-up", type: "button",
          ...(path.length === 0 ? { disabled: "" } : {}),
          events: { click: () => flame.zoomOut() } },
        "← Zoom out");
      breadcrumbEl.appendChild(upBtn);
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
      renderFlameDiagnostics(status, totalSamples, ps.mode);

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

  // -- IPFlow -------------------------------------------------------

  PANELS.ipflow = (function () {
    const ps = state.panels.ipflow = {
      mode: "live",            // "live" | "scrub" | "paused"
      historyMinutes: 60,      // timeline X-axis span
      topN: 50,                // server-side IP filter
      layout: "numeric",       // "numeric" | "spread"
      speed: 60,               // playback speed multiplier (scrub)
      scrubStart: null,        // epoch seconds (only valid in scrub mode)
      source: "",              // "" = all sources
      activeBtn: "live",       // which preset is "lit"
    };
    let reqsCanvas, bytesCanvas, reqsCursor, bytesCursor, topoCanvas;
    let animator, statusEl, legendEl, sourceSel;
    let _knownSources = [];

    function _setLive() {
      ps.mode = "live";
      ps.activeBtn = "live";
      ps.scrubStart = null;
      if (animator) {
        animator.clearReplay();
        animator.setLive();
      }
      _updateButtonStates();
      _refresh().catch(showError);
    }

    function _replay(secondsBack) {
      const start = (Date.now() / 1000) - secondsBack;
      ps.activeBtn = secondsBack === 86400 ? "24h" : "1h";
      _startScrub(start);
    }

    function _startScrub(t) {
      ps.mode = "scrub";
      ps.scrubStart = t;
      if (animator) {
        animator.clearReplay();
        animator.startScrub(t, ps.speed);
      }
      _updateButtonStates();
      _refresh().catch(showError);
    }

    function _togglePause() {
      if (!animator) return;
      if (ps.mode === "paused") {
        ps.mode = "scrub";
        ps.activeBtn = ps.scrubStart != null ? null : "live";
        animator.resume();
      } else {
        ps.mode = "paused";
        ps.activeBtn = "pause";
        animator.pause();
      }
      _updateButtonStates();
      _updateStatus();
    }

    function _onCursorEvent(e) {
      ps.activeBtn = null;     // clicked-on-timeline scrub: no preset lit
      _startScrub(e.detail.t);
    }

    function _updateButtonStates() {
      const map = {
        "live":  state.panels.ipflow.btnLive,
        "1h":    state.panels.ipflow.btn1h,
        "24h":   state.panels.ipflow.btn24h,
        "pause": state.panels.ipflow.btnPause,
      };
      for (const k in map) {
        if (map[k]) map[k].classList.toggle("active", ps.activeBtn === k);
      }
    }

    function _populateSources(list) {
      // Re-render the dropdown only if the list of sources actually
      // changed; otherwise it'd reset the user's mid-selection focus
      // on every poll. Always include "" / all as the first option.
      const incoming = JSON.stringify(list || []);
      if (incoming === JSON.stringify(_knownSources)) return;
      _knownSources = list ? list.slice() : [];
      if (!sourceSel) return;
      const cur = ps.source;
      sourceSel.innerHTML = "";
      const optAll = document.createElement("option");
      optAll.value = ""; optAll.textContent = "all";
      sourceSel.appendChild(optAll);
      for (const s of _knownSources) {
        const o = document.createElement("option");
        o.value = s; o.textContent = s;
        sourceSel.appendChild(o);
      }
      // Preserve the user's selection if still present, otherwise
      // fall back to "all".
      sourceSel.value =
        (_knownSources.includes(cur) ? cur : "");
      if (sourceSel.value !== cur) ps.source = sourceSel.value;
    }

    function _updateStatus() {
      if (!statusEl) return;
      if (!animator) { statusEl.textContent = ""; return; }
      if (ps.mode === "paused") { statusEl.textContent = "paused"; return; }
      if (ps.mode === "scrub") {
        const ph = animator.playhead;
        const t = new Date(ph * 1000).toTimeString().slice(0, 8);
        statusEl.textContent = `scrub @ ${t}  ${ps.speed}×`;
      } else {
        statusEl.textContent = "live";
      }
    }

    function _onPlayhead(ph) {
      if (reqsCursor && reqsCanvas) {
        smIPFlow.positionCursor(reqsCursor, reqsCanvas, ph);
      }
      if (bytesCursor && bytesCanvas) {
        smIPFlow.positionCursor(bytesCursor, bytesCanvas, ph);
      }
      _updateStatus();
      // Transition out of scrub when the playhead catches up to
      // the live position (wallclock minus the live lag), so the
      // cursor doesn't jump backwards by LAG seconds when modes
      // switch. Threshold matches LIVE_LAG in ipflow.js.
      if (ps.mode === "scrub") {
        const liveEdge = (Date.now() / 1000) - 10;
        if (ph >= liveEdge) _setLive();
      }
    }

    return {
      title: "IP Flow",
      init(host) {
        host.replaceChildren();
        const panel = el("section", { class: "panel" });

        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "IP Flow"),
          el("label", null, "service",
            (sourceSel = (function () {
              const s = el("select", { class: "control",
                                          id: "ipflow-source" });
              s.addEventListener("change", () => {
                ps.source = s.value;
                _refresh().catch(showError);
              });
              // Single "all" option until the first /api/ipflow/timeline
              // response replaces it with the live source list.
              s.appendChild(el("option", { value: "" }, "all"));
              return s;
            })())),
          el("label", null, "history",
            selectInput("ipflow-hist", String(ps.historyMinutes),
              [["15","15m"],["60","1h"],["360","6h"],["1440","24h"]].map(
                ([v,l]) => ({value: v, label: l})),
              v => { ps.historyMinutes = +v; _refresh().catch(showError); })),
          el("label", null, "speed",
            selectInput("ipflow-speed", String(ps.speed),
              [["1","1×"],["10","10×"],["60","60×"],["300","300×"],["1800","1800×"]]
                .map(([v,l]) => ({value: v, label: l})),
              v => {
                ps.speed = +v;
                if (animator) animator.setSpeed(ps.speed);
                _updateStatus();
              })),
          el("label", null, "layout",
            selectInput("ipflow-layout", ps.layout,
              [["numeric","numeric"],["spread","spread"]].map(
                ([v,l]) => ({value: v, label: l})),
              v => {
                ps.layout = v;
                if (animator) animator.setLayout(v);
              })),
          el("label", null, "top",
            selectInput("ipflow-top", String(ps.topN),
              [["10","10"],["25","25"],["50","50"],["100","100"],["0","all"]].map(
                ([v,l]) => ({value: v, label: l})),
              v => { ps.topN = +v; _refresh().catch(showError); })),
          (state.panels.ipflow.btnLive = el("button",
            { class: "btn", "data-mode-btn": "live",
              events: { click: _setLive } }, "Live")),
          (state.panels.ipflow.btn1h = el("button",
            { class: "btn", "data-mode-btn": "replay-3600",
              events: { click: () => _replay(3600) } }, "Replay 1h")),
          (state.panels.ipflow.btn24h = el("button",
            { class: "btn", "data-mode-btn": "replay-86400",
              events: { click: () => _replay(86400) } }, "Replay 24h")),
          (state.panels.ipflow.btnPause = el("button",
            { class: "btn", "data-mode-btn": "paused",
              events: { click: _togglePause } }, "Pause")),
          (statusEl = el("span", { class: "muted ipflow-status" })),
        );
        panel.appendChild(ctrls);

        const chartRow = el("div", { class: "ipflow-charts" });
        reqsCanvas = el("canvas", { class: "ipflow-chart" });
        bytesCanvas = el("canvas", { class: "ipflow-chart" });
        reqsCursor = el("div", { class: "ipflow-cursor" });
        bytesCursor = el("div", { class: "ipflow-cursor" });
        chartRow.appendChild(el("div", { class: "ipflow-chart-wrap" },
          el("div", { class: "muted ipflow-chart-title" }, "requests / minute"),
          reqsCanvas, reqsCursor));
        chartRow.appendChild(el("div", { class: "ipflow-chart-wrap" },
          el("div", { class: "muted ipflow-chart-title" }, "bytes / minute"),
          bytesCanvas, bytesCursor));
        panel.appendChild(chartRow);

        legendEl = el("div", { class: "ipflow-legend" });
        smIPFlow.buildLegend(legendEl);
        panel.appendChild(legendEl);

        topoCanvas = el("canvas", { class: "ipflow-topo" });
        panel.appendChild(topoCanvas);

        host.appendChild(panel);

        smIPFlow.attachTimelineClick(reqsCanvas);
        smIPFlow.attachTimelineClick(bytesCanvas);
        reqsCanvas.addEventListener("ipflow-cursor", _onCursorEvent);
        bytesCanvas.addEventListener("ipflow-cursor", _onCursorEvent);

        animator = smIPFlow.IPFlowAnimator(topoCanvas, {
          onPlayhead: _onPlayhead,
        });
        animator.setLayout(ps.layout);
        ps.activeBtn = "live";
        // The sourceSel was just rebuilt fresh with only "all";
        // clear the closure-scoped dedup cache so the next
        // /api/ipflow/timeline response actually rebuilds the
        // dropdown options. Without this, navigating away from IP
        // Flow and back would leave the dropdown stuck at "all"
        // because the cached list still matched the server's reply.
        _knownSources = [];
        _updateButtonStates();
        _updateStatus();
      },
      async refresh() { await _refresh(); },
    };

    async function _refresh() {
      const tl = await getJSON("/api/ipflow/timeline",
        { minutes: ps.historyMinutes, source: ps.source });
      _populateSources(tl.sources || []);
      const buckets = tl.buckets || [];
      smIPFlow.drawTimeline(reqsCanvas, buckets, {
        key: "reqs", fmtY: fmtCount,
      });
      smIPFlow.drawTimeline(bytesCanvas, buckets, {
        key: "bytes", fmtY: fmtBytes,
        lineColor: "#f5b041",
        fillColor: "rgba(245, 176, 65, 0.18)",
      });

      if (ps.mode === "paused") { _updateStatus(); return; }

      let win;
      const baseParams = { top_n: ps.topN, source: ps.source };
      if (ps.mode === "scrub" && ps.scrubStart != null) {
        const ph = animator ? animator.playhead : ps.scrubStart;
        const seconds = Math.max(60,
          Math.ceil((Date.now() / 1000) - ph));
        win = await getJSON("/api/ipflow/window",
          Object.assign({ start: ph, seconds }, baseParams));
        if (animator) {
          animator.setIPs(win.ips || {});
          animator.addRecords(win.requests || [], "scrub");
        }
      } else {
        win = await getJSON("/api/ipflow/window",
          Object.assign({ seconds: 60 }, baseParams));
        if (animator) {
          animator.setIPs(win.ips || {});
          animator.addRecords(win.requests || [], "live");
        }
      }
      _updateStatus();
    }
  })();

  // -- Countries ----------------------------------------------------

  PANELS.countries = (function () {
    const ps = state.panels.countries = {
      minutes: 60, topN: 12,
    };
    let chartCanvas, tbody, hostEl, statusEl, emptyEl;
    return {
      title: "Countries",
      init(host) {
        hostEl = host;
        host.replaceChildren();
        const panel = el("section", { class: "panel" });

        const ctrls = el("div", { class: "panel-controls" },
          el("span", { class: "panel-title" }, "Countries"),
          el("label", null, "history",
            selectInput("countries-mins", String(ps.minutes),
              [["15","15m"],["60","1h"],["360","6h"],["1440","24h"]].map(
                ([v,l]) => ({value: v, label: l})),
              v => { ps.minutes = +v; refresh(); })),
          el("label", null, "top",
            selectInput("countries-top", String(ps.topN),
              [["6","6"],["8","8"],["12","12"],["20","20"],["50","50"]].map(
                ([v,l]) => ({value: v, label: l})),
              v => { ps.topN = +v; refresh(); })),
          (statusEl = el("span", { class: "muted ipflow-status" })),
        );
        panel.appendChild(ctrls);

        chartCanvas = el("canvas", { class: "countries-chart" });
        panel.appendChild(chartCanvas);

        const table = el("table", { class: "data-table" },
          el("thead", null, el("tr", null,
            ...["country","reqs","bytes","err %","ips","top IPs"]
              .map((h, i) => el("th",
                { class: i === 0 ? "" : "num" }, h)))),
          el("tbody"));
        tbody = table.querySelector("tbody");
        panel.appendChild(table);

        emptyEl = el("div", { class: "panel-empty hidden" },
          el("p", null, "No country database loaded."),
          el("p", { class: "muted" },
            "Pass --country-db PATH (or place RIR delegated-stats files "
            + "under /var/lib/smartmet-monitor/) and restart smwebmon. "
            + "Files are downloaded from each RIR's ftp.* server "
            + "(apnic, ripe, arin, lacnic, afrinic)."));
        panel.appendChild(emptyEl);

        host.appendChild(panel);
      },
      async refresh() { await refresh(); },
    };

    async function refresh() {
      const status = await getJSON("/api/countries/status");
      if (!status.enabled) {
        emptyEl.classList.remove("hidden");
        chartCanvas.classList.add("hidden");
        statusEl.textContent = "(no country DB)";
        tbody.replaceChildren();
        return;
      }
      emptyEl.classList.add("hidden");
      chartCanvas.classList.remove("hidden");
      statusEl.textContent =
        `${status.netblocks_v4 + status.netblocks_v6} netblocks`;

      const tl = await getJSON("/api/countries/timeline",
        { minutes: ps.minutes, top_n: ps.topN });
      const series = (tl.series || []).map(s => ({
        label: s.label,
        color: s.label === "other"
                ? "rgba(155,175,198,0.55)"
                : smChart.colorFor(s.label),
        values: s.values,
      }));
      smChart.drawLineMulti(chartCanvas, series, {
        fmtY: smChart.formatCount,
        last_ts: tl.ts && tl.ts.length ? tl.ts[tl.ts.length - 1] : null,
        step_seconds: 60,
      });

      const tab = await getJSON("/api/countries",
        { minutes: ps.minutes, top_n: ps.topN });
      const rows = tab.rows || [];
      tbody.replaceChildren();
      for (const r of rows) {
        const topIpsHtml = (r.top_ips || []).map(t =>
          `<span class="cc-ip">${escHtml(t.ip)} (${t.count})</span>`
        ).join(" ");
        const tr = el("tr", null,
          el("td", null,
            el("span", { class: "cc-dot",
                          style: `background:${smChart.colorFor(r.cc)}` }),
            r.cc),
          el("td", { class: "num" }, fmtCount(r.reqs)),
          el("td", { class: "num" }, fmtBytes(r.bytes)),
          el("td", { class: "num " + errColor(r.err_pct) },
            r.err_pct.toFixed(2)),
          el("td", { class: "num" }, String(r.ips)),
          el("td", { class: "muted",
                      html: topIpsHtml }),
        );
        tbody.appendChild(tr);
      }
    }
  })();

  // =================================================================
  // boot, tab strip, polling
  // =================================================================

  function _writeHash() {
    const cluster = state.activeCluster
      ? `cluster=${encodeURIComponent(state.activeCluster)}/` : "";
    location.hash = `#/${cluster}${state.active || ""}`;
  }

  function _parseHash() {
    // #/cluster=NAME/panel — cluster part optional.
    const h = (location.hash || "").replace(/^#\//, "");
    const m = h.match(/^cluster=([^/]+)\/(.*)$/);
    if (m) return { cluster: decodeURIComponent(m[1]), panel: m[2] || null };
    return { cluster: null, panel: h || null };
  }

  function activateCluster(name) {
    if (state.activeCluster === name) return;
    state.activeCluster = name;
    const sel = document.getElementById("cluster-select");
    if (sel) sel.value = name == null ? "" : name;
    _writeHash();
    _topologyCache = null;          // force a redraw under the new name
    _refreshTopology().catch(() => {});
    // Re-render the current panel against the new cluster's store.
    if (state.active) {
      const id = state.active;
      state.active = null;          // force re-init in activatePanel
      activatePanel(id);
    }
  }

  function activatePanel(id) {
    if (state.active === id) return;
    state.active = id;
    _writeHash();
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
    setupCardCollapse(host);
    PANELS[id].refresh().then(() => setupCardCollapse(host)).catch(showError);
  }

  function tickActive() {
    _refreshReplayBanner().catch(() => {});
    if (!state.active) return;
    const host = document.getElementById("panel-host");
    PANELS[state.active].refresh()
      .then(() => { setupCardCollapse(host); setIndicator("live", "live"); })
      .catch(e => showError(e));
    if (state.modalRefresh) state.modalRefresh().catch(showError);
  }

  // ---- Replay-progress banner ------------------------------------
  //
  // Polls /api/health every refresh tick; while replay.in_progress
  // is true, surfaces a top-of-page strip with elapsed time and the
  // file count. The dashboard's panels are still empty during this
  // window because replay_logs is a single bulk_load that doesn't
  // populate the store incrementally — operators were confused by
  // panels showing "(no data)" for several seconds when an RPM
  // upgrade restarted smwebmon mid-day.

  async function _refreshReplayBanner() {
    const el = document.getElementById("replay-banner");
    if (!el) return;
    let h;
    try {
      h = await getJSON("/api/health");
    } catch (e) {
      el.classList.add("hidden");
      return;
    }
    const r = (h && h.replay) || {};
    const txt = el.querySelector(".replay-text");
    if (r.in_progress) {
      el.classList.remove("hidden");
      const elapsed = r.started_at
        ? Math.max(0, Math.round(Date.now() / 1000 - r.started_at))
        : 0;
      const rotated = r.include_rotated ? " (incl. rotated)" : "";
      const total = r.files_total || 0;
      const done = r.files_done || 0;
      const cur = r.current_file ? r.current_file.split("/").pop() : "";
      let parts = [`Replaying logs${rotated}`];
      if (total) parts.push(`${done} / ${total} files`);
      if (cur) parts.push(`current: ${cur}`);
      parts.push(`${elapsed}s elapsed`);
      txt.textContent = parts.join(" — ");
    } else {
      el.classList.add("hidden");
    }
  }

  function showError(e) {
    setIndicator("error: " + (e.message || e), "error");
    console.error(e);
  }

  async function _loadClusters() {
    // /api/clusters always returns 200 — empty list means single-host
    // mode (no clusters.conf, or empty config), populated list means
    // cluster mode is active and the dashboard should show the
    // selector.
    try {
      const r = await fetch("/api/clusters", { cache: "no-store" });
      if (!r.ok) return [];
      return (await r.json()).clusters || [];
    } catch (e) {
      return [];
    }
  }

  function _renderClusterSelector(clusters) {
    const sel = document.getElementById("cluster-select");
    if (!sel) return;
    sel.innerHTML = "";
    if (!clusters.length) {
      sel.classList.add("hidden");
      return;
    }
    sel.classList.remove("hidden");
    for (const c of clusters) {
      const opt = el("option", { value: c.name },
        `${c.name} (${c.alive_count}/${c.backend_count})`);
      if (c.alive_count === 0 && c.backend_count > 0) {
        opt.classList.add("cluster-down");
      }
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => activateCluster(sel.value));
  }

  // ---- Cluster topology strip ------------------------------------
  //
  // Renders one pill per backend below the topbar in cluster mode.
  // Shape: [● c1] [● c2] [✗ c3] [● v1.q3] — color-hashed dot ties
  // each backend's identity across every chart (a backend in the
  // chart legend has the same color as its topology pill). A down
  // backend (registered prefix, no body in clusterinfo) renders as
  // muted with a strikethrough; hovering any pill surfaces the
  // backend's handler list so the operator can answer "which
  // services does v1.q3 actually run?" without leaving the view.

  let _topologyCache = null;

  async function _refreshTopology() {
    const strip = document.getElementById("cluster-topology");
    if (!strip) return;
    if (!state.activeCluster) {
      strip.classList.add("hidden");
      strip.replaceChildren();
      _topologyCache = null;
      return;
    }
    let topo;
    try {
      topo = await getJSON("/api/cluster/topology",
        { cluster: state.activeCluster });
    } catch (e) {
      strip.classList.add("hidden");
      return;
    }
    // Avoid re-rendering identical state — pill hovers and the
    // operator's right-click position would be lost on every 30 s
    // refresh otherwise.
    const sig = JSON.stringify({
      n: topo.name, s: topo.discovery_status,
      b: (topo.backends || []).map(b => [b.prefix, b.alive,
                                          (b.handlers || []).length]),
    });
    if (sig === _topologyCache) return;
    _topologyCache = sig;

    strip.classList.remove("hidden");
    strip.replaceChildren();

    const head = el("span", { class: "topo-head" },
      el("span", { class: "topo-cluster" }, topo.name),
      el("span", { class: "topo-status muted" }, topo.discovery_status));
    strip.appendChild(head);

    const pillBox = el("span", { class: "topo-pills" });
    for (const b of (topo.backends || [])) {
      const handlers = b.handlers || [];
      const handlerCount = handlers.length;
      const tooltip = handlers.length
            ? `${b.prefix}: ${handlerCount} handler${handlerCount === 1 ? "" : "s"}\n` +
              handlers.slice(0, 40).join("\n") +
              (handlers.length > 40 ? `\n…and ${handlers.length - 40} more` : "")
            : `${b.prefix}: no handlers (down or draining)`;
      const pill = el("span",
        { class: "topo-pill" + (b.alive ? "" : " down"),
          title: tooltip });
      pill.appendChild(el("span", { class: "topo-dot",
        style: `background:${b.alive ? smChart.colorFor(b.prefix) : "transparent"}` }));
      pill.appendChild(document.createTextNode(b.prefix));
      pill.appendChild(el("span", { class: "topo-count muted" },
        b.alive ? String(handlerCount) : "—"));
      pillBox.appendChild(pill);
    }
    strip.appendChild(pillBox);
  }

  async function boot() {
    setIndicator("…");

    // Discover clusters first so the URL-hash routing (which can
    // include a cluster=NAME) matches what the server knows.
    state.clusters = await _loadClusters();
    _renderClusterSelector(state.clusters);

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

    // Pick initial cluster + panel from URL hash. Fallbacks: first
    // cluster (or single-host), URLs panel.
    const parsed = _parseHash();
    const validCluster =
      parsed.cluster && state.clusters.some(c => c.name === parsed.cluster)
        ? parsed.cluster
        : (state.clusters[0] ? state.clusters[0].name : null);
    state.activeCluster = validCluster;
    if (validCluster) {
      const sel = document.getElementById("cluster-select");
      if (sel) sel.value = validCluster;
    }

    fetch("/api/health" + (validCluster
        ? "?cluster=" + encodeURIComponent(validCluster) : ""),
        { cache: "no-store" })
      .then(r => r.json())
      .then(h => {
        document.getElementById("status").textContent =
          h.admin_hosts.length
            ? `hosts: ${h.admin_hosts.join(", ")}`
            : "no admin hosts configured";
      })
      .catch(() => {});

    const initialPanel =
      panels.some(p => p.id === parsed.panel) ? parsed.panel : "urls";
    activatePanel(initialPanel);
    _refreshTopology().catch(() => {});
    _refreshReplayBanner().catch(() => {});

    // Periodically refresh the cluster list (alive counts in the
    // dropdown drift as backends come/go) without thrashing.
    setInterval(async () => {
      const fresh = await _loadClusters();
      // Only re-render if the set / counts changed, to avoid
      // dropping the user's mid-selection focus.
      if (JSON.stringify(fresh) !== JSON.stringify(state.clusters)) {
        state.clusters = fresh;
        _renderClusterSelector(fresh);
        if (state.activeCluster) {
          const sel = document.getElementById("cluster-select");
          if (sel) sel.value = state.activeCluster;
        }
      }
      _refreshTopology().catch(() => {});
    }, 30 * 1000);

    state.poll = setInterval(tickActive, REFRESH_MS);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
