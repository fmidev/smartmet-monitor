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

  // Filled line chart with axis labels (full chrome).
  function drawLine(canvas, values, opts = {}) {
    const { ctx, w, h } = setupHiDPI(canvas);
    const padL = 36, padR = 8, padT = 6, padB = 16;
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;

    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, w, h);

    if (!values || !values.length) {
      ctx.fillStyle = PALETTE.axis;
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("(no data)", padL, padT + 14);
      return;
    }

    const valid = values.filter(v => Number.isFinite(v));
    const vmax = valid.length ? Math.max(...valid) : 1;
    const yScale = vmax > 0 ? innerH / vmax : 0;

    ctx.strokeStyle = PALETTE.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT + innerH);
    ctx.lineTo(padL + innerW, padT + innerH);
    ctx.stroke();

    ctx.fillStyle = PALETTE.axis;
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    ctx.fillText(opts.fmtY ? opts.fmtY(vmax) : formatMs(vmax),
                  padL - 4, padT + 6);
    ctx.fillText("0", padL - 4, padT + innerH);

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

    if (opts.xLabels && opts.xLabels.length === 2) {
      ctx.fillStyle = PALETTE.axis;
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

  function formatMs(v) {
    if (v == null || !Number.isFinite(v)) return "";
    if (v <= 0) return "0";
    if (v < 1)  return v.toFixed(2) + "ms";
    if (v < 10) return v.toFixed(1) + "ms";
    if (v < 1000) return Math.round(v) + "ms";
    return (v / 1000).toFixed(1) + "s";
  }

  function formatBytes(b) {
    if (b == null) return "";
    const u = ["B", "K", "M", "G", "T"];
    let v = b, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v >= 100 ? `${v.toFixed(0)}${u[i]}` : `${v.toFixed(1)}${u[i]}`;
  }

  function formatCount(n) {
    if (n == null) return "";
    if (n < 10000) return String(n);
    if (n < 1e6) return (n / 1e3).toFixed(1) + "k";
    if (n < 1e9) return (n / 1e6).toFixed(1) + "M";
    return (n / 1e9).toFixed(1) + "G";
  }

  global.smChart = {
    drawLine, drawSparkline, drawHistogram, applyHbar,
    formatMs, formatBytes, formatCount,
    PALETTE,
  };
})(window);
