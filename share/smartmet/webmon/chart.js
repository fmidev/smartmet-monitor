// chart.js — tiny Canvas helpers (line + histogram). No external libs.
//
// All functions take an HTMLCanvasElement and devicePixelRatio-aware
// data; they paint the canvas and return. They do not retain state.

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
  };

  // Resize for HiDPI: bump backing-store resolution while CSS keeps
  // the laid-out width. Without this, charts look blurry on retina.
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

  // Filled line chart with a single y-axis label (max). Pads `values`
  // with NaNs accepted as gaps.
  function drawLine(canvas, values, opts = {}) {
    const { ctx, w, h } = setupHiDPI(canvas);
    const padL = 36, padR = 8, padT = 6, padB = 16;
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;

    // Background grid.
    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, w, h);

    if (!values.length) {
      ctx.fillStyle = PALETTE.axis;
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("(no data)", padL, padT + 14);
      return;
    }

    const valid = values.filter(v => Number.isFinite(v));
    const vmax = valid.length ? Math.max(...valid) : 1;
    const yScale = vmax > 0 ? innerH / vmax : 0;

    // Axis line.
    ctx.strokeStyle = PALETTE.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT + innerH);
    ctx.lineTo(padL + innerW, padT + innerH);
    ctx.stroke();

    // Y-axis labels: max at top, 0 at bottom.
    ctx.fillStyle = PALETTE.axis;
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    ctx.fillText(formatMs(vmax), padL - 4, padT + 6);
    ctx.fillText("0",            padL - 4, padT + innerH);

    // Line + fill.
    const stepX = values.length > 1 ? innerW / (values.length - 1) : 0;
    ctx.strokeStyle = opts.lineColor || PALETTE.line;
    ctx.fillStyle   = opts.fillColor || PALETTE.fill;
    ctx.lineWidth = 1.5;

    ctx.beginPath();
    let started = false;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      const x = padL + i * stepX;
      if (!Number.isFinite(v)) {
        started = false;
        continue;
      }
      const y = padT + innerH - v * yScale;
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();

    // Fill under the line.
    ctx.beginPath();
    ctx.moveTo(padL, padT + innerH);
    started = false;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      const x = padL + i * stepX;
      if (!Number.isFinite(v)) {
        ctx.lineTo(x, padT + innerH);
        started = false;
        continue;
      }
      const y = padT + innerH - v * yScale;
      if (!started) {
        ctx.lineTo(x, padT + innerH);
        ctx.lineTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.lineTo(padL + innerW, padT + innerH);
    ctx.closePath();
    ctx.fill();

    // X-axis labels (just two — leftmost and rightmost minute counts).
    if (opts.xLabels && opts.xLabels.length === 2) {
      ctx.textAlign = "left";
      ctx.fillText(opts.xLabels[0], padL, h - 2);
      ctx.textAlign = "right";
      ctx.fillText(opts.xLabels[1], padL + innerW, h - 2);
    }
  }

  // Bar chart for an exponential-bucket histogram. `buckets` is
  // [{lo_ms, hi_ms, count}]. We draw each bar with width proportional
  // to log(hi/lo), so adjacent buckets are visibly different.
  function drawHistogram(canvas, buckets, opts = {}) {
    const { ctx, w, h } = setupHiDPI(canvas);
    const padL = 56, padR = 8, padT = 6, padB = 26;
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;

    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, w, h);

    if (!buckets.length) {
      ctx.fillStyle = PALETTE.axis;
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("(no requests in window)", padL, padT + 14);
      return;
    }

    const counts = buckets.map(b => b.count);
    const cmax = Math.max(...counts, 1);

    // Each bar has equal width — the bucket boundaries are
    // exponential anyway, so equal-pixel bars give a readable
    // logarithmic x-axis.
    const barW = innerW / buckets.length;

    // Axes.
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

    // Bars.
    ctx.fillStyle = opts.barColor || PALETTE.bar;
    for (let i = 0; i < buckets.length; i++) {
      const c = buckets[i].count;
      const barH = (c / cmax) * innerH;
      const x = padL + i * barW;
      const y = padT + innerH - barH;
      ctx.fillRect(x + 0.5, y, Math.max(1, barW - 1), barH);
    }

    // X-axis labels: leftmost and rightmost bucket bounds.
    ctx.fillStyle = PALETTE.axis;
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
    ctx.fillText(formatMs(buckets[0].lo_ms), padL, h - 8);
    ctx.textAlign = "right";
    ctx.fillText(formatMs(buckets[buckets.length - 1].hi_ms),
                  padL + innerW, h - 8);
  }

  function formatMs(v) {
    if (v <= 0) return "0";
    if (v < 1)  return v.toFixed(2) + "ms";
    if (v < 10) return v.toFixed(1) + "ms";
    if (v < 1000) return Math.round(v) + "ms";
    return (v / 1000).toFixed(1) + "s";
  }

  global.smChart = { drawLine, drawHistogram, formatMs };
})(window);
