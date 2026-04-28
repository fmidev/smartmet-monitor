// app.js — smartmet-webmon front-end. Polls /api/urls, renders the
// table, and manages the drill-in modal. No build step, no bundler;
// vanilla JS only.

(function () {
  "use strict";

  const REFRESH_MS = 2000;

  const els = {
    status:    document.getElementById("status"),
    indicator: document.getElementById("refresh-indicator"),
    window:    document.getElementById("urls-window"),
    sort:      document.getElementById("urls-sort"),
    filter:    document.getElementById("urls-filter"),
    count:     document.getElementById("urls-count"),
    tbody:     document.querySelector("#urls-table tbody"),
    modalOverlay: document.getElementById("modal-overlay"),
    modalTitle: document.getElementById("modal-title"),
    modalClose: document.getElementById("modal-close"),
    detailWindows: document.querySelector("#detail-windows tbody"),
    detailStatus:  document.querySelector("#detail-status tbody"),
    detailKeys:    document.querySelector("#detail-keys tbody"),
    detailChart:   document.getElementById("detail-chart"),
    detailHist:    document.getElementById("detail-hist"),
  };

  const state = {
    selectedUrl: null,
    pollTimer:   null,
    fetching:    false,
  };

  // ---- helpers -----------------------------------------------------

  function fmtMs(v) {
    if (v == null) return "";
    if (v <= 0) return "0";
    if (v < 1)  return v.toFixed(2);
    if (v < 10) return v.toFixed(1);
    return Math.round(v).toString();
  }
  function fmtBytes(b) {
    if (b == null) return "";
    const u = ["B", "K", "M", "G", "T"];
    let v = b, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v >= 100 ? `${v.toFixed(0)}${u[i]}` : `${v.toFixed(1)}${u[i]}`;
  }
  function fmtCount(n) {
    if (n == null) return "";
    if (n < 10000) return String(n);
    if (n < 1e6) return (n / 1e3).toFixed(1) + "k";
    return (n / 1e6).toFixed(1) + "M";
  }
  function latColor(ms) {
    if (ms == null) return "";
    if (ms < 100)  return "lat-good";
    if (ms < 1000) return "lat-warn";
    return "lat-bad";
  }
  function errColor(pct) {
    return pct >= 1 ? "err-bad" : "";
  }
  function setIndicator(text, cls) {
    els.indicator.textContent = text;
    els.indicator.className = "indicator " + (cls || "");
  }

  // ---- main URLs table polling ------------------------------------

  async function fetchUrls() {
    if (state.fetching) return;
    state.fetching = true;
    setIndicator("…");
    const params = new URLSearchParams({
      window: els.window.value,
      sort:   els.sort.value,
      reverse: "1",
      filter: els.filter.value || "",
    });
    try {
      const r = await fetch("/api/urls?" + params.toString(),
                            { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      renderUrls(data);
      setIndicator("live", "live");
    } catch (e) {
      setIndicator("error: " + e.message, "error");
    } finally {
      state.fetching = false;
    }
  }

  function renderUrls(data) {
    els.count.textContent = `${data.rows.length} URL${
      data.rows.length === 1 ? "" : "s"}`;
    const frag = document.createDocumentFragment();
    for (const r of data.rows) {
      const tr = document.createElement("tr");
      if (r.url === state.selectedUrl) tr.classList.add("selected");
      tr.dataset.url = r.url;
      tr.innerHTML = `
        <td class="url" title="${escapeHtml(r.url)}">${escapeHtml(r.url)}</td>
        <td class="num">${fmtCount(r.count)}</td>
        <td class="num ${latColor(r.mean_ms)}">${fmtMs(r.mean_ms)}</td>
        <td class="num ${latColor(r.p50_ms)}">${fmtMs(r.p50_ms)}</td>
        <td class="num ${latColor(r.p95_ms)}">${fmtMs(r.p95_ms)}</td>
        <td class="num ${latColor(r.p99_ms)}">${fmtMs(r.p99_ms)}</td>
        <td class="num ${latColor(r.max_ms)}">${fmtMs(r.max_ms)}</td>
        <td class="num">${fmtBytes(r.avg_bytes)}</td>
        <td class="num">${fmtBytes(r.total_bytes)}</td>
        <td class="num ${errColor(r.err_pct)}">${r.err_pct.toFixed(1)}</td>
      `;
      tr.addEventListener("click", () => openDetail(r.url));
      frag.appendChild(tr);
    }
    els.tbody.replaceChildren(frag);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---- drill-in modal ----------------------------------------------

  async function openDetail(url) {
    state.selectedUrl = url;
    els.modalTitle.textContent = url;
    els.modalOverlay.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    await refreshDetail();
  }

  function closeDetail() {
    state.selectedUrl = null;
    els.modalOverlay.classList.add("hidden");
    document.body.style.overflow = "";
  }

  async function refreshDetail() {
    if (!state.selectedUrl) return;
    const win = els.window.value;
    try {
      const [detail, chart] = await Promise.all([
        fetch(`/api/urls/detail?url=${encodeURIComponent(state.selectedUrl)}` +
              `&window=${encodeURIComponent(win)}`,
              { cache: "no-store" }).then(r => r.json()),
        fetch(`/api/urls/chart?url=${encodeURIComponent(state.selectedUrl)}` +
              `&window=60`,
              { cache: "no-store" }).then(r => r.json()),
      ]);
      renderDetail(detail);
      renderChart(chart);
    } catch (e) {
      // Leave whatever was there last; the next tick will retry.
      console.error("detail fetch failed:", e);
    }
  }

  function renderDetail(d) {
    if (!d.found) {
      els.detailWindows.innerHTML =
        `<tr><td colspan="10" class="muted">no data for this URL (yet)</td></tr>`;
      els.detailStatus.innerHTML = "";
      els.detailKeys.innerHTML = "";
      smChart.drawHistogram(els.detailHist, []);
      return;
    }

    const wf = document.createDocumentFragment();
    for (const w of d.windows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${w.window_min}m</td>
        <td class="num">${fmtCount(w.count)}</td>
        <td class="num ${latColor(w.mean_ms)}">${fmtMs(w.mean_ms)}</td>
        <td class="num ${latColor(w.p50_ms)}">${fmtMs(w.p50_ms)}</td>
        <td class="num ${latColor(w.p95_ms)}">${fmtMs(w.p95_ms)}</td>
        <td class="num ${latColor(w.p99_ms)}">${fmtMs(w.p99_ms)}</td>
        <td class="num ${latColor(w.max_ms)}">${fmtMs(w.max_ms)}</td>
        <td class="num">${fmtBytes(w.avg_bytes)}</td>
        <td class="num">${fmtBytes(w.total_bytes)}</td>
        <td class="num ${errColor(w.err_pct)}">${w.err_pct.toFixed(1)}</td>
      `;
      wf.appendChild(tr);
    }
    els.detailWindows.replaceChildren(wf);

    const sf = document.createDocumentFragment();
    for (const s of d.status_codes) {
      const tr = document.createElement("tr");
      const cls = s.status >= 500 ? "err-bad" :
                   s.status >= 400 ? "lat-warn" : "lat-good";
      tr.innerHTML = `
        <td class="${cls}">${s.status}</td>
        <td class="num">${fmtCount(s.count)}</td>
        <td class="num">${s.pct.toFixed(1)}</td>
      `;
      sf.appendChild(tr);
    }
    els.detailStatus.replaceChildren(sf);

    const kf = document.createDocumentFragment();
    for (const k of d.apikeys) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(k.key)}</td>
        <td class="num">${fmtCount(k.count)}</td>
      `;
      kf.appendChild(tr);
    }
    els.detailKeys.replaceChildren(kf);

    smChart.drawHistogram(els.detailHist, d.histogram.buckets);
  }

  function renderChart(c) {
    const values = c.found ? c.values.map(v => Number(v)) : [];
    smChart.drawLine(els.detailChart, values, {
      xLabels: ["-60m", "now"],
    });
  }

  // ---- wiring ------------------------------------------------------

  els.window.addEventListener("change", () => { fetchUrls(); refreshDetail(); });
  els.sort.addEventListener("change", fetchUrls);
  els.filter.addEventListener("input", debounce(fetchUrls, 250));
  els.modalClose.addEventListener("click", closeDetail);
  els.modalOverlay.addEventListener("click", (e) => {
    if (e.target === els.modalOverlay) closeDetail();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.selectedUrl) closeDetail();
  });

  function debounce(fn, ms) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function tick() {
    fetchUrls();
    if (state.selectedUrl) refreshDetail();
  }

  fetch("/api/health", { cache: "no-store" })
    .then(r => r.json())
    .then(h => {
      els.status.textContent = h.admin_hosts.length
        ? `hosts: ${h.admin_hosts.join(", ")}`
        : "no admin hosts configured";
    })
    .catch(() => { els.status.textContent = "(unreachable)"; });

  tick();
  state.pollTimer = setInterval(tick, REFRESH_MS);
})();
