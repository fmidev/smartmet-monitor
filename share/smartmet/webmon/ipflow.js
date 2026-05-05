// ipflow.js — IP-flow panel rendering.
//
// Two pieces:
//   * drawTimeline(canvas, buckets, opts) — req/min or bytes/min line
//     chart with optional cursor marker. Click dispatches a CustomEvent
//     "ipflow-cursor" with `detail.t` (epoch seconds) so the panel can
//     pan into scrub mode.
//   * IPFlowAnimator(canvas) — RAF-driven particle field. Each request
//     becomes a circle that flies from its IP's slot on the rim to the
//     centre over its `dur_ms`. Speed encodes latency; colour encodes
//     status; radius encodes log(bytes).

(function (global) {
  "use strict";

  const PALETTE = (global.smChart && global.smChart.PALETTE) || {
    bg: "#11161d", grid: "#222a35", axis: "#9aa6b2",
    line: "#5dade2", fill: "rgba(93,173,226,0.18)",
    label: "#cbd5e0",
  };

  // ---- timeline ---------------------------------------------------

  const TL_PAD = { l: 56, r: 8, t: 6, b: 18 };

  function _setupHiDPI(canvas) {
    const dpr = global.devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width));
    const h = Math.max(1, Math.round(r.height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  function _niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    if (n <= 1) return 1 * p;
    if (n <= 2) return 2 * p;
    if (n <= 5) return 5 * p;
    return 10 * p;
  }

  function drawTimeline(canvas, buckets, opts = {}) {
    const { ctx, w, h } = _setupHiDPI(canvas);
    const innerW = w - TL_PAD.l - TL_PAD.r;
    const innerH = h - TL_PAD.t - TL_PAD.b;
    const fmtY = opts.fmtY || (v => String(v));

    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, w, h);

    if (!buckets || !buckets.length) {
      ctx.fillStyle = PALETTE.axis;
      ctx.font = "12px ui-monospace, monospace";
      ctx.fillText("(no data)", TL_PAD.l, TL_PAD.t + 14);
      canvas._tlState = null;
      return;
    }

    const key = opts.key || "reqs";
    const valid = buckets.map(b => +b[key] || 0);
    const dataMax = Math.max(1, ...valid);
    const vmax = _niceMax(dataMax);
    const yScale = innerH / vmax;
    const t0 = buckets[0].t;
    const t1 = buckets[buckets.length - 1].t;
    const span = Math.max(1, t1 - t0);
    const xOf = t => TL_PAD.l + ((t - t0) / span) * innerW;
    const yOf = v => TL_PAD.t + innerH - v * yScale;

    // Axes + horizontal grid lines.
    ctx.strokeStyle = PALETTE.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(TL_PAD.l, TL_PAD.t + innerH);
    ctx.lineTo(TL_PAD.l + innerW, TL_PAD.t + innerH);
    ctx.stroke();
    const gridLines = 4;
    ctx.strokeStyle = "rgba(42, 49, 60, 0.55)";
    for (let i = 1; i < gridLines; i++) {
      const y = TL_PAD.t + innerH - (innerH * i) / gridLines;
      ctx.beginPath();
      ctx.moveTo(TL_PAD.l, y);
      ctx.lineTo(TL_PAD.l + innerW, y);
      ctx.stroke();
    }

    ctx.fillStyle = PALETTE.axis;
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    for (let i = 0; i <= gridLines; i++) {
      const v = (vmax * i) / gridLines;
      ctx.fillText(fmtY(v), TL_PAD.l - 4, yOf(v));
    }

    // Time labels at the left, middle, right.
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const fmtT = sec => {
      const d = new Date(sec * 1000);
      return d.toTimeString().slice(0, 5);
    };
    ctx.fillText(fmtT(t0), TL_PAD.l, TL_PAD.t + innerH + 2);
    ctx.fillText(fmtT((t0 + t1) / 2), TL_PAD.l + innerW / 2,
                 TL_PAD.t + innerH + 2);
    ctx.fillText(fmtT(t1), TL_PAD.l + innerW, TL_PAD.t + innerH + 2);

    // Filled line.
    ctx.strokeStyle = opts.lineColor || PALETTE.line;
    ctx.fillStyle   = opts.fillColor || PALETTE.fill;
    ctx.lineWidth = 1.5;

    ctx.beginPath();
    for (let i = 0; i < buckets.length; i++) {
      const b = buckets[i];
      const x = xOf(b.t);
      const y = yOf(+b[key] || 0);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(xOf(t0), TL_PAD.t + innerH);
    for (let i = 0; i < buckets.length; i++) {
      const b = buckets[i];
      ctx.lineTo(xOf(b.t), yOf(+b[key] || 0));
    }
    ctx.lineTo(xOf(t1), TL_PAD.t + innerH);
    ctx.closePath();
    ctx.fill();

    // Cursor.
    if (opts.cursor != null
        && opts.cursor >= t0 - 60 && opts.cursor <= t1 + 60) {
      const cx = xOf(Math.max(t0, Math.min(t1, opts.cursor)));
      ctx.strokeStyle = "#f5b041";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(cx, TL_PAD.t);
      ctx.lineTo(cx, TL_PAD.t + innerH);
      ctx.stroke();
    }

    canvas._tlState = { t0, t1, innerW, innerH };
  }

  function _attachTimelineClick(canvas) {
    if (canvas._tlClickWired) return;
    canvas._tlClickWired = true;
    canvas.addEventListener("click", e => {
      const s = canvas._tlState;
      if (!s) return;
      const r = canvas.getBoundingClientRect();
      const x = e.clientX - r.left;
      const f = (x - TL_PAD.l) / s.innerW;
      if (f < 0 || f > 1) return;
      const t = s.t0 + f * (s.t1 - s.t0);
      canvas.dispatchEvent(new CustomEvent("ipflow-cursor",
        { detail: { t }, bubbles: true }));
    });
  }

  // ---- topology animator -----------------------------------------

  function _statusColor(status) {
    if (status >= 500) return "#e74c3c";
    if (status >= 400) return "#f5b041";
    if (status >= 300) return "#5dade2";
    return "#58d68d";
  }

  function _radiusForBytes(b) {
    // log scale: 0 → 1.5 px, 1KB → ~3 px, 1MB → ~6 px, 1GB → ~9 px.
    const v = Math.log10(Math.max(1, b + 1));
    return Math.max(1.5, Math.min(12, 1.5 + v * 1.2));
  }

  function IPFlowAnimator(canvas) {
    const self = {
      canvas,
      ips: {},                // ip -> { angle, count, bytes, hot }
      particles: [],          // active particles
      seen: new Map(),        // (t.toFixed(3) + ip) -> insertedAt anim-t
      paused: false,
      mode: "live",
      windowStart: null,      // for scrub mode
      windowSeconds: 60,
      speed: 1.0,
      _raf: null,
      _baseAnimT: null,       // performance.now()/1000 at last setWindow
      _baseRecordT: null,     // newest record ts at last setWindow
      _lastDraw: 0,
    };

    function setWindow(data, opts = {}) {
      self.ips = data.ips || {};
      const recs = data.requests || [];
      const now = performance.now() / 1000;
      const mode = opts.mode || "live";
      self.mode = mode;

      if (mode === "scrub") {
        // Map record_t → anim_t starting "now". Records earlier than
        // window start fall outside the playback timeline.
        const start = opts.windowStart != null
          ? opts.windowStart : (recs.length ? recs[0].t : now);
        self.windowStart = start;
        self.windowSeconds = opts.windowSeconds || 60;
        self.speed = opts.speed || 1.0;
        self._baseRecordT = start;
        self._baseAnimT = now;
        // Reset particle list — scrub jumps replace the field.
        self.particles = [];
        self.seen.clear();
        for (const r of recs) {
          self.particles.push(_makeParticle(r,
            now + (r.t - start) / self.speed,
            (r.dur_ms / 1000) / self.speed));
        }
      } else {
        // Live mode — append only records we haven't seen yet, spawning
        // them at "now". This makes the polling loop additive: a 2 s
        // poll + a request that took 5 s spawns once and lives across
        // multiple polls without re-creation.
        const seen = self.seen;
        const cutoff = now - 60.0;       // forget tracking for very old
        for (const [k, t] of seen) {
          if (t < cutoff) seen.delete(k);
        }
        for (const r of recs) {
          const key = r.t.toFixed(3) + "|" + r.ip;
          if (seen.has(key)) continue;
          seen.set(key, now);
          self.particles.push(_makeParticle(r, now, r.dur_ms / 1000));
        }
        self.windowStart = null;
      }
      _requestDraw();
    }

    function _makeParticle(rec, spawnAnimT, life) {
      const ipMeta = self.ips[rec.ip] || { angle: 0 };
      return {
        ip: rec.ip,
        angle: (ipMeta.angle * Math.PI) / 180,
        spawn: spawnAnimT,
        life: Math.max(0.05, life),     // sub-50 ms requests animate visibly
        bytes: rec.bytes,
        status: rec.status,
        radius: _radiusForBytes(rec.bytes),
        color: _statusColor(rec.status),
      };
    }

    function _draw() {
      self._raf = null;
      const { ctx, w, h } = _setupHiDPI(self.canvas);
      ctx.fillStyle = PALETTE.bg;
      ctx.fillRect(0, 0, w, h);

      const cx = w / 2, cy = h / 2;
      const R = Math.max(40, Math.min(w, h) / 2 - 24);

      // Rim.
      ctx.strokeStyle = "rgba(155, 175, 198, 0.18)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.stroke();

      // IP slots — tick + label for hot IPs (top by count).
      const ipList = Object.entries(self.ips)
        .sort((a, b) => b[1].count - a[1].count);
      const hot = new Set(ipList.slice(0, 12).map(([ip]) => ip));
      ctx.font = "10px ui-monospace, monospace";
      ctx.textBaseline = "middle";
      for (const [ip, meta] of ipList) {
        const a = (meta.angle * Math.PI) / 180;
        const x = cx + Math.cos(a) * R;
        const y = cy + Math.sin(a) * R;
        ctx.fillStyle = "rgba(155, 175, 198, 0.55)";
        ctx.beginPath();
        ctx.arc(x, y, 1.5, 0, Math.PI * 2);
        ctx.fill();
        if (hot.has(ip)) {
          ctx.fillStyle = "#cbd5e0";
          const lx = cx + Math.cos(a) * (R + 8);
          const ly = cy + Math.sin(a) * (R + 8);
          // Anchor label so it doesn't overlap the rim from the inside.
          ctx.textAlign = (Math.cos(a) >= 0) ? "left" : "right";
          ctx.fillText(ip, lx, ly);
        }
      }

      // Particles.
      const now = performance.now() / 1000;
      const live = [];
      for (const p of self.particles) {
        const elapsed = now - p.spawn;
        if (elapsed < 0) { live.push(p); continue; }
        const progress = elapsed / p.life;
        if (progress > 1.05) continue;        // expired
        live.push(p);
        const r = R * (1 - Math.min(1, progress));
        const x = cx + Math.cos(p.angle) * r;
        const y = cy + Math.sin(p.angle) * r;
        ctx.fillStyle = p.color;
        ctx.globalAlpha = progress > 1 ? Math.max(0, 1 - (progress - 1) * 20) : 1;
        ctx.beginPath();
        ctx.arc(x, y, p.radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      self.particles = live;

      // Centre disc.
      ctx.fillStyle = "#3a4f70";
      ctx.beginPath();
      ctx.arc(cx, cy, 6, 0, Math.PI * 2);
      ctx.fill();

      // Stats overlay (top-left).
      ctx.fillStyle = PALETTE.axis;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.font = "11px ui-monospace, monospace";
      const ipCount = ipList.length;
      const partCount = self.particles.length;
      const modeLabel = self.paused ? "paused"
                       : (self.mode === "scrub" ? "scrub" : "live");
      ctx.fillText(`${modeLabel}  ips:${ipCount}  particles:${partCount}`,
                   8, 8);

      // Keep ticking while we have particles or while live (so the
      // next poll's records have a frame to land into).
      if (!self.paused
          && (self.particles.length > 0 || self.mode === "live")) {
        _requestDraw();
      }
    }

    function _requestDraw() {
      if (self._raf != null) return;
      self._raf = requestAnimationFrame(_draw);
    }

    function pause() {
      self.paused = true;
      if (self._raf != null) {
        cancelAnimationFrame(self._raf);
        self._raf = null;
      }
      _requestDraw();          // one final render to refresh the label
    }
    function resume() { self.paused = false; _requestDraw(); }
    function destroy() {
      if (self._raf != null) cancelAnimationFrame(self._raf);
      self._raf = null;
      self.particles = [];
    }

    _requestDraw();

    return { setWindow, pause, resume, destroy,
             get particles() { return self.particles; },
             get paused() { return self.paused; },
             get mode() { return self.mode; } };
  }

  global.smIPFlow = {
    drawTimeline,
    attachTimelineClick: _attachTimelineClick,
    IPFlowAnimator,
  };
})(window);
