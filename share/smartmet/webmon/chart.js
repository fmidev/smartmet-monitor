// chart.js — Canvas helpers (line, sparkline, histogram, hbar). No
// external libs. All functions take an HTMLCanvasElement and paint
// it; they don't retain state.

(function (global) {
  "use strict";

  const PALETTE = {
    bg:    "#0e1116",
    grid:  "#2a313c",
    axis:  "#7c8794",
    line:  "#58a6ff",
    fill:  "rgba(88, 166, 255, 0.18)",
    bar:   "#58a6ff",
    label: "#b9c4d0",
    good:  "#7ee787",
    warn:  "#d29922",
    bad:   "#f85149",
    accent2: "#d2a8ff",
  };

  // Tableau 10 categorical palette (Brewer-quality contrast at small
  // sizes, color-blind friendly). 10 slots is enough for FMI's largest
  // cluster (6 backends + a few specialised pseudo-backends like
  // v1.q3 / v2.q3); larger clusters wrap.
  const CATEGORICAL = [
    "#4e79a7", "#f28e2c", "#e15759", "#76b7b2", "#59a14f",
    "#edc949", "#af7aa1", "#ff9da7", "#9c755f", "#bab0ab",
  ];

  // Stable color assignment by hashing the label — `c2` gets the same
  // color across every panel, every refresh, every browser session.
  // Operators learn one mapping cluster-wide instead of having to
  // re-orient per chart.
  function colorFor(label) {
    let h = 0;
    const s = String(label);
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    }
    return CATEGORICAL[Math.abs(h) % CATEGORICAL.length];
  }

  // Resize for HiDPI: bump backing-store resolution while CSS keeps
  // the laid-out width.
  function setupHiDPI(canvas) {
    const dpr = global.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || canvas.width;
    const cssH = canvas.clientHeight || canvas.height;
    canvas.width  = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    return { ctx, w: cssW, h: cssH };
  }

  // "Nice" tick selection — Heckbert's algorithm from "Nice numbers
  // for graph labels" (Graphics Gems I, 1990). Two passes: first
  // nice-round the range with a *loose* threshold ladder, then nice-
  // round the step (range / (maxTicks − 1)) with a *round* ladder.
  // Ticks land at 1, 2, or 5 times a power of 10.
  //
  // Why Heckbert rather than qdtools/main/qdstat.cpp's autotick (a
  // 2 / 5 / 10 ladder used here previously for codebase consistency)?
  // qdstat's algorithm is correct for **histogram bin boundaries**,
  // where users want "double the bins / halve the bins" granularity —
  // that workflow naturally lands on the 2 / 5 / 10 ladder. **Chart
  // axis labels** are a different problem: operators expect 0, 1, 2,
  // 3, 4 on a chart whose data peaks at 5, not 0, 2, 4, 6 with the
  // axis overshooting to 6. The 1 multiplier fills the gap. Concrete
  // case where it matters:
  //
  //                        qdstat (2/5/10)         Heckbert (1/2/5/10)
  //   vmax=5,  maxBins=5    0, 2, 4, 6              0, 1, 2, 3, 4, 5
  //   vmax=50, maxBins=5    0, 20, 40, 60           0, 10, 20, 30, 40, 50
  //
  // The 1/2/5/10 ladder is also the de-facto standard across chart
  // libraries (matplotlib, d3, plotly, R's pretty()), so the choice
  // matches what operators have learned to read elsewhere. qdstat
  // stays unchanged — its 2/5/10 ladder is right for histogram bins.
  function _niceNumber(range, round) {
    if (!Number.isFinite(range) || range <= 0) return 1;
    const exponent = Math.floor(Math.log10(range));
    const magnitude = Math.pow(10, exponent);
    const fraction = range / magnitude;            // fraction ∈ [1, 10)
    let nf;
    if (round) {
      // Picking a tick step: the boundaries (1.5, 3, 7) bias towards
      // the more granular choice when the input is near a midpoint.
      // E.g. fraction = 1.4 → tick = 1, fraction = 1.6 → tick = 2.
      if      (fraction < 1.5) nf = 1;
      else if (fraction < 3)   nf = 2;
      else if (fraction < 7)   nf = 5;
      else                     nf = 10;
    } else {
      // Picking the loose range: thresholds at the ladder values
      // themselves so we can fully contain the data.
      if      (fraction <= 1) nf = 1;
      else if (fraction <= 2) nf = 2;
      else if (fraction <= 5) nf = 5;
      else                    nf = 10;
    }
    return nf * magnitude;
  }

  // Autoscale [0, vmax] to nice tick boundaries. Y-axis ticks always
  // start at 0 in our charts — every plotted metric is non-negative
  // (request rates, latencies, byte counts, error percentages).
  // Returns { ticks, niceMax, step }; the topmost tick is niceMax,
  // which is ≥ vmax so the data line never quite touches the chart's
  // top edge.
  function _niceTicks(vmax, maxTicks) {
    if (!Number.isFinite(vmax) || vmax <= 0) {
      return { ticks: [0, 1], niceMax: 1, step: 1 };
    }
    const range = _niceNumber(vmax, false);
    const step  = _niceNumber(range / Math.max(2, maxTicks - 1), true);
    const niceMax = step * Math.ceil(vmax / step);
    // Iterate by integer count to avoid float accumulation drift over
    // the tick stride.
    const n = Math.round(niceMax / step);
    const ticks = [];
    for (let i = 0; i <= n; i++) ticks.push(i * step);
    return { ticks, niceMax, step };
  }

  // Filled line chart with axis labels and interactive hover. Pass
  // either opts.ts (one timestamp per value) or opts.last_ts +
  // opts.step_seconds; the chart computes per-point timestamps and
  // surfaces them via mousemove. opts.fmtY formats the y-axis label
  // and the tooltip value (default formatMs).
  function drawLine(canvas, values, opts = {}) {
    const { ctx, w, h } = setupHiDPI(canvas);
    const padL = 44, padR = 8, padT = 6, padB = 16;
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;

    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, w, h);

    if (!values || !values.length) {
      ctx.fillStyle = PALETTE.axis;
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("(no data)", padL, padT + 14);
      canvas._chartState = null;
      return;
    }

    const valid = values.filter(v => Number.isFinite(v));
    const dataMax = valid.length ? Math.max(...valid) : 1;
    // Approx one tick label per ~28 px of vertical space, capped at
    // 6 (more than that is just clutter on the small charts in the
    // Proc / Network grids).
    const desired = Math.max(2, Math.min(6, Math.floor(innerH / 28)));
    const { ticks: yTicks, niceMax } =
        _niceTicks(dataMax || 1, desired);
    const vmax = niceMax;
    const yScale = vmax > 0 ? innerH / vmax : 0;

    // Bottom axis + subtle horizontal gridlines at each interior tick.
    ctx.strokeStyle = PALETTE.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT + innerH);
    ctx.lineTo(padL + innerW, padT + innerH);
    ctx.stroke();
    ctx.strokeStyle = "rgba(42, 49, 60, 0.55)";
    for (let i = 1; i < yTicks.length - 1; i++) {
      const y = padT + innerH - yTicks[i] * yScale;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + innerW, y);
      ctx.stroke();
    }

    // Y-axis labels at every tick.
    ctx.fillStyle = PALETTE.axis;
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    const fmtY = opts.fmtY || formatMs;
    for (const v of yTicks) {
      const y = padT + innerH - v * yScale;
      ctx.fillText(fmtY(v), padL - 4, y);
    }

    const stepX = values.length > 1 ? innerW / (values.length - 1) : 0;
    ctx.strokeStyle = opts.lineColor || PALETTE.line;
    ctx.fillStyle   = opts.fillColor || PALETTE.fill;
    ctx.lineWidth = 1.5;

    ctx.beginPath();
    let started = false;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      const x = padL + i * stepX;
      if (!Number.isFinite(v)) { started = false; continue; }
      const y = padT + innerH - v * yScale;
      if (!started) { ctx.moveTo(x, y); started = true; }
      else { ctx.lineTo(x, y); }
    }
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(padL, padT + innerH);
    started = false;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      const x = padL + i * stepX;
      if (!Number.isFinite(v)) { ctx.lineTo(x, padT + innerH); started = false; continue; }
      const y = padT + innerH - v * yScale;
      if (!started) { ctx.lineTo(x, padT + innerH); ctx.lineTo(x, y); started = true; }
      else { ctx.lineTo(x, y); }
    }
    ctx.lineTo(padL + innerW, padT + innerH);
    ctx.closePath();
    ctx.fill();

    // Build a per-point timestamp array if we can. Either the caller
    // supplies opts.ts (one entry per value, e.g. ProcSample.ts), or
    // last_ts + step_seconds for evenly-spaced series. Without either
    // we fall back to the legacy "two static labels" chrome.
    let ts = null;
    if (opts.ts && opts.ts.length === values.length) {
      ts = opts.ts;
    } else if (opts.last_ts != null && opts.step_seconds) {
      const lt = +opts.last_ts;
      const st = +opts.step_seconds;
      ts = values.map((_, i) => lt - (values.length - 1 - i) * st);
    }

    ctx.fillStyle = PALETTE.axis;
    ctx.textBaseline = "alphabetic";
    if (ts && ts.length >= 2) {
      // 5 ticks if we have room, 3 on narrow charts.
      const tickCount = innerW >= 360 ? 5 : 3;
      for (let t = 0; t < tickCount; t++) {
        const idx = Math.round(t * (values.length - 1) / (tickCount - 1));
        const x = padL + idx * stepX;
        ctx.textAlign = t === 0 ? "left"
                      : t === tickCount - 1 ? "right"
                      : "center";
        ctx.fillText(formatTimeShort(ts[idx]), x, h - 2);
      }
    } else if (opts.xLabels && opts.xLabels.length === 2) {
      ctx.textAlign = "left";
      ctx.fillText(opts.xLabels[0], padL, h - 2);
      ctx.textAlign = "right";
      ctx.fillText(opts.xLabels[1], padL + innerW, h - 2);
    }
    if (opts.title) {
      ctx.fillStyle = PALETTE.label;
      ctx.textAlign = "left";
      ctx.fillText(opts.title, padL, padT - 2);
    }

    // Stash everything the hover handler needs so it can find which
    // data point the cursor is over without rerunning the layout
    // arithmetic.
    canvas._chartState = {
      values, ts, vmax, opts,
      padL, padT, innerW, innerH, stepX,
    };

    // If the cursor is still over the chart (refresh ticks redraw
    // every 2 s while the operator hovers), restore the overlay so
    // the tooltip + crosshair don't blink off.
    if (canvas._hoverIdx != null) {
      const idx = Math.max(0, Math.min(values.length - 1,
                                        canvas._hoverIdx));
      _drawHoverOverlay(canvas, idx);
    }

    if (!canvas._chartWired) {
      canvas.addEventListener("mousemove", _chartHover);
      canvas.addEventListener("mouseleave", _chartLeave);
      canvas._chartWired = true;
    }
  }

  function _drawHoverOverlay(canvas, idx) {
    const s = canvas._chartState;
    if (!s) return;
    const ctx = canvas.getContext("2d");
    const x = s.padL + idx * s.stepX;

    // Vertical guide line at the cursor's data point.
    ctx.save();
    ctx.strokeStyle = "rgba(217, 225, 234, 0.45)";
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, s.padT);
    ctx.lineTo(x, s.padT + s.innerH);
    ctx.stroke();
    ctx.restore();

    // Crosshair dot at the value point.
    const v = s.values[idx];
    if (Number.isFinite(v) && s.vmax > 0) {
      const y = s.padT + s.innerH - (v / s.vmax) * s.innerH;
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(x, y, 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function _chartHover(e) {
    const canvas = this;
    const s = canvas._chartState;
    if (!s) return;
    const rect = canvas.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    if (cssX < s.padL - 6 || cssX > s.padL + s.innerW + 6) {
      _chartLeave.call(canvas);
      return;
    }
    const idx = s.stepX > 0
        ? Math.max(0, Math.min(s.values.length - 1,
                                Math.round((cssX - s.padL) / s.stepX)))
        : 0;
    canvas._hoverIdx = idx;

    // Redraw the chart cleanly (this also re-applies the overlay
    // because drawLine sees _hoverIdx is set).
    drawLine(canvas, s.values, s.opts);

    const v = s.values[idx];
    const fmtY = s.opts.fmtY || formatMs;
    const valueStr = Number.isFinite(v) ? fmtY(v) : "—";
    const timeStr = s.ts ? formatTimeFull(s.ts[idx]) : "";
    const tooltip = _chartTooltipEl();
    tooltip.innerHTML =
      (timeStr ? `<span class="ct-time">${_esc(timeStr)}</span> ` : "") +
      `<span class="ct-value">${_esc(valueStr)}</span>`;
    tooltip.classList.remove("hidden");
    tooltip.style.left = (e.clientX + 14) + "px";
    tooltip.style.top  = (e.clientY + 14) + "px";
  }

  function _chartLeave() {
    const canvas = this;
    if (canvas._hoverIdx == null) return;
    canvas._hoverIdx = null;
    const s = canvas._chartState;
    if (s) drawLine(canvas, s.values, s.opts);
    _chartTooltipEl().classList.add("hidden");
  }

  function _chartTooltipEl() {
    let el = document.getElementById("chart-tooltip");
    if (!el) {
      el = document.createElement("div");
      el.id = "chart-tooltip";
      el.className = "chart-tooltip hidden";
      document.body.appendChild(el);
    }
    return el;
  }

  function _esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function formatTimeShort(epochSeconds) {
    const d = new Date(epochSeconds * 1000);
    const pad = n => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function formatTimeFull(epochSeconds) {
    const d = new Date(epochSeconds * 1000);
    const pad = n => String(n).padStart(2, "0");
    const today = new Date();
    const sameDay = d.getFullYear() === today.getFullYear()
                  && d.getMonth() === today.getMonth()
                  && d.getDate() === today.getDate();
    const hms = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    if (sameDay) return hms;
    const ymd = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
    return `${ymd} ${hms}`;
  }

  // Multi-line variant of drawLine. Each entry in `series` is
  // ``{label, color, values}``. All values arrays should align
  // index-by-index (same timestamp metadata applies to every line).
  // Y-axis nice-ticks are derived from the union max across all series.
  // Hover shows a vertical guide + a dot per line at the cursor's
  // index, plus a multi-row tooltip listing every series' value
  // sorted descending so the busiest backend is at the top.
  function drawLineMulti(canvas, series, opts = {}) {
    const { ctx, w, h } = setupHiDPI(canvas);
    const padL = 44, padR = 8, padT = 6, padB = 16;
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;

    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, w, h);

    series = (series || []).filter(s => s && s.values);
    if (!series.length) {
      ctx.fillStyle = PALETTE.axis;
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("(no data)", padL, padT + 14);
      canvas._chartState = null;
      return;
    }

    // Length is the longest series; shorter series render as fewer
    // segments anchored at the right edge. (In practice the cluster's
    // per-backend buffers are length-aligned by the source, so this
    // pad-with-undefined is defensive.)
    let n = 0;
    for (const s of series) if (s.values.length > n) n = s.values.length;

    let dataMax = 0;
    for (const s of series) {
      for (const v of s.values) {
        if (Number.isFinite(v) && v > dataMax) dataMax = v;
      }
    }
    const desiredTicks = Math.max(2, Math.min(6, Math.floor(innerH / 28)));
    const { ticks: yTicks, niceMax } =
        _niceTicks(dataMax || 1, desiredTicks);
    const yScale = niceMax > 0 ? innerH / niceMax : 0;

    // Bottom axis + interior gridlines.
    ctx.strokeStyle = PALETTE.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT + innerH);
    ctx.lineTo(padL + innerW, padT + innerH);
    ctx.stroke();
    ctx.strokeStyle = "rgba(42, 49, 60, 0.55)";
    for (let i = 1; i < yTicks.length - 1; i++) {
      const y = padT + innerH - yTicks[i] * yScale;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + innerW, y);
      ctx.stroke();
    }

    // Y labels.
    const fmtY = opts.fmtY || formatMs;
    ctx.fillStyle = PALETTE.axis;
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    for (const v of yTicks) {
      const y = padT + innerH - v * yScale;
      ctx.fillText(fmtY(v), padL - 4, y);
    }

    const stepX = n > 1 ? innerW / (n - 1) : 0;

    // Each series — line only (no fill, multi-line fills overlap and
    // become unreadable).
    ctx.lineWidth = 1.5;
    for (const s of series) {
      ctx.strokeStyle = s.color || colorFor(s.label);
      ctx.beginPath();
      let started = false;
      const offset = n - s.values.length;   // align to right edge
      for (let i = 0; i < s.values.length; i++) {
        const v = s.values[i];
        const x = padL + (i + offset) * stepX;
        if (!Number.isFinite(v)) { started = false; continue; }
        const y = padT + innerH - v * yScale;
        if (!started) { ctx.moveTo(x, y); started = true; }
        else { ctx.lineTo(x, y); }
      }
      ctx.stroke();
    }

    // Build the timestamp array (same logic as drawLine).
    let ts = null;
    if (opts.ts && opts.ts.length === n) {
      ts = opts.ts;
    } else if (opts.last_ts != null && opts.step_seconds) {
      const lt = +opts.last_ts;
      const st = +opts.step_seconds;
      ts = Array.from({ length: n }, (_, i) => lt - (n - 1 - i) * st);
    }

    ctx.fillStyle = PALETTE.axis;
    ctx.textBaseline = "alphabetic";
    if (ts && ts.length >= 2) {
      const tickCount = innerW >= 360 ? 5 : 3;
      for (let t = 0; t < tickCount; t++) {
        const idx = Math.round(t * (n - 1) / (tickCount - 1));
        const x = padL + idx * stepX;
        ctx.textAlign = t === 0 ? "left"
                      : t === tickCount - 1 ? "right"
                      : "center";
        ctx.fillText(formatTimeShort(ts[idx]), x, h - 2);
      }
    }
    if (opts.title) {
      ctx.fillStyle = PALETTE.label;
      ctx.textAlign = "left";
      ctx.fillText(opts.title, padL, padT - 2);
    }

    canvas._chartState = {
      multi: true,
      series, ts, niceMax, opts,
      padL, padT, innerW, innerH, stepX, n,
    };
    if (canvas._hoverIdx != null) {
      const idx = Math.max(0, Math.min(n - 1, canvas._hoverIdx));
      _drawHoverOverlayMulti(canvas, idx);
    }
    if (!canvas._chartWired) {
      canvas.addEventListener("mousemove", _chartHover);
      canvas.addEventListener("mouseleave", _chartLeave);
      canvas._chartWired = true;
    }
  }

  function _drawHoverOverlayMulti(canvas, idx) {
    const s = canvas._chartState;
    if (!s) return;
    const ctx = canvas.getContext("2d");
    const x = s.padL + idx * s.stepX;

    // Vertical guide.
    ctx.save();
    ctx.strokeStyle = "rgba(217, 225, 234, 0.45)";
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, s.padT);
    ctx.lineTo(x, s.padT + s.innerH);
    ctx.stroke();
    ctx.restore();

    // Dot per series at the cursor's index.
    if (s.niceMax > 0) {
      for (const ser of s.series) {
        const offset = s.n - ser.values.length;
        const localIdx = idx - offset;
        if (localIdx < 0 || localIdx >= ser.values.length) continue;
        const v = ser.values[localIdx];
        if (!Number.isFinite(v)) continue;
        const y = s.padT + s.innerH - (v / s.niceMax) * s.innerH;
        ctx.fillStyle = ser.color || colorFor(ser.label);
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "#0e1116";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }
  }

  // Sparkline — chromeless line, fills its container. Auto-scales
  // per call so each row can show its own shape regardless of others.
  function drawSparkline(canvas, values, opts = {}) {
    const { ctx, w, h } = setupHiDPI(canvas);
    ctx.fillStyle = "transparent";
    ctx.clearRect(0, 0, w, h);

    if (!values || !values.length) return;
    const valid = values.filter(v => Number.isFinite(v));
    const vmax = valid.length ? Math.max(...valid) : 0;
    if (vmax <= 0) return;

    const stepX = values.length > 1 ? w / (values.length - 1) : 0;
    ctx.strokeStyle = opts.color || PALETTE.line;
    ctx.lineWidth = 1.25;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (!Number.isFinite(v)) { started = false; continue; }
      const x = i * stepX;
      const y = h - (v / vmax) * (h - 2) - 1;
      if (!started) { ctx.moveTo(x, y); started = true; }
      else { ctx.lineTo(x, y); }
    }
    ctx.stroke();
  }

  // Histogram for exponential-bucket data: [{lo_ms, hi_ms, count}].
  function drawHistogram(canvas, buckets, opts = {}) {
    const { ctx, w, h } = setupHiDPI(canvas);
    const padL = 56, padR = 8, padT = 6, padB = 26;
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;

    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, w, h);

    if (!buckets || !buckets.length) {
      ctx.fillStyle = PALETTE.axis;
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("(no requests in window)", padL, padT + 14);
      return;
    }

    const counts = buckets.map(b => b.count);
    const cmax = Math.max(...counts, 1);
    const barW = innerW / buckets.length;

    ctx.strokeStyle = PALETTE.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT);
    ctx.lineTo(padL, padT + innerH);
    ctx.lineTo(padL + innerW, padT + innerH);
    ctx.stroke();

    ctx.fillStyle = PALETTE.axis;
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    ctx.fillText(String(cmax), padL - 4, padT + 6);
    ctx.fillText("0",          padL - 4, padT + innerH);

    ctx.fillStyle = opts.barColor || PALETTE.bar;
    for (let i = 0; i < buckets.length; i++) {
      const c = buckets[i].count;
      const barH = (c / cmax) * innerH;
      const x = padL + i * barW;
      const y = padT + innerH - barH;
      ctx.fillRect(x + 0.5, y, Math.max(1, barW - 1), barH);
    }

    ctx.fillStyle = PALETTE.axis;
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
    ctx.fillText(formatMs(buckets[0].lo_ms), padL, h - 8);
    ctx.textAlign = "right";
    ctx.fillText(formatMs(buckets[buckets.length - 1].hi_ms),
                  padL + innerW, h - 8);
  }

  // Horizontal bar — paint a div with fill width = pct%, choosing a
  // color class by latency-color-style threshold. Used inside table
  // cells where a Canvas would be overkill.
  function applyHbar(elem, value, max, opts = {}) {
    const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
    const cls = opts.classFor ? opts.classFor(value) : "";
    elem.innerHTML =
      `<div class="hbar"><div class="hbar-fill ${cls}" style="width:${pct}%"></div></div>`;
  }

  // Trim trailing-zero fractions: parseFloat normalises "1.00" → "1",
  // "1.50" → "1.5". Used by the format helpers below so a nice-tick
  // value of 1 renders as "1ms", not "1.0ms".
  function _trim(n, decimals) {
    if (!Number.isFinite(n)) return "";
    return parseFloat(n.toFixed(decimals)).toString();
  }

  function formatMs(v) {
    if (v == null || !Number.isFinite(v)) return "";
    if (v <= 0) return "0";
    if (v >= 1000) return _trim(v / 1000, 1) + "s";
    if (v >= 10)   return Math.round(v) + "ms";
    if (v >= 1)    return _trim(v, 1) + "ms";
    return _trim(v, 2) + "ms";
  }

  function formatBytes(b) {
    if (b == null) return "";
    const u = ["B", "K", "M", "G", "T"];
    let v = b, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v >= 100 ? `${Math.round(v)}${u[i]}` : `${_trim(v, 1)}${u[i]}`;
  }

  function formatCount(n) {
    if (n == null) return "";
    if (n < 10000) return String(n);
    if (n < 1e6)   return _trim(n / 1e3, 1) + "k";
    if (n < 1e9)   return _trim(n / 1e6, 1) + "M";
    return _trim(n / 1e9, 1) + "G";
  }

  global.smChart = {
    drawLine, drawLineMulti, drawSparkline, drawHistogram, applyHbar,
    formatMs, formatBytes, formatCount,
    colorFor,
    PALETTE,
    CATEGORICAL,
  };
})(window);
