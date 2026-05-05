// ipflow.js — IP-flow panel rendering.
//
// Three pieces:
//   * drawTimeline(canvas, buckets, opts) — req/min or bytes/min line
//     chart with optional cursor marker. Click dispatches a
//     "ipflow-cursor" CustomEvent with detail.t (epoch seconds) so
//     the panel can pan into scrub mode.
//   * IPFlowAnimator(canvas, options) — playhead-driven particle
//     field. The animator owns a record-time clock that walks
//     forward at `speed × wallclock` (in scrub mode) or stays
//     pinned to the newest data (in live mode); particles spawn as
//     the playhead crosses each record's `t`. Decouples the panel's
//     polling cadence from the visual flow rate.
//   * drawLegend(el) — DOM legend strip with the colour / speed /
//     size encoding.

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

    canvas._tlState = {
      t0, t1, innerW, innerH,
      padL: TL_PAD.l, padT: TL_PAD.t,
    };
  }

  function _attachTimelineClick(canvas) {
    if (canvas._tlClickWired) return;
    canvas._tlClickWired = true;
    canvas.addEventListener("click", e => {
      const s = canvas._tlState;
      if (!s) return;
      const r = canvas.getBoundingClientRect();
      const x = e.clientX - r.left;
      const f = (x - s.padL) / s.innerW;
      if (f < 0 || f > 1) return;
      const t = s.t0 + f * (s.t1 - s.t0);
      canvas.dispatchEvent(new CustomEvent("ipflow-cursor",
        { detail: { t }, bubbles: true }));
    });
  }

  // Position a CSS-positioned cursor div over a chart canvas.
  // The wrap element is `position: relative`; the cursor div is
  // `position: absolute`. Returns the pixel-x of the cursor (or
  // null when out of range), so the panel can hide the cursor when
  // the playhead falls outside the chart's time range.
  function positionCursor(cursorDiv, canvas, t) {
    const s = canvas._tlState;
    if (!s || t == null) {
      cursorDiv.style.display = "none";
      return null;
    }
    // Always render the cursor: clamp to the chart's edge if the
    // playhead happens to fall outside the rendered time range
    // (e.g. live mode with no fresh data — playhead = wallclock
    // now but the rightmost bucket is older). Hiding it confused
    // operators who expected the timestamp readout and the
    // vertical line to agree.
    const span = Math.max(1, s.t1 - s.t0);
    const clamped = Math.max(s.t0, Math.min(s.t1, t));
    const x = s.padL + ((clamped - s.t0) / span) * s.innerW;
    cursorDiv.style.display = "";
    cursorDiv.style.left = x + "px";
    cursorDiv.style.top = s.padT + "px";
    cursorDiv.style.height = s.innerH + "px";
    return x;
  }

  // ---- topology animator -----------------------------------------

  function _statusColor(status) {
    if (status >= 500) return "#e74c3c";
    if (status >= 400) return "#f5b041";
    if (status >= 300) return "#5dade2";
    return "#58d68d";
  }

  function _radiusForBytes(b) {
    const v = Math.log10(Math.max(1, b + 1));
    return Math.max(1.5, Math.min(12, 1.5 + v * 1.2));
  }

  // Min visible particle lifetime in animation seconds. At very
  // high replay speeds a 100 ms request would otherwise traverse
  // the radius in 0.17 ms — invisible. Floor at 200 ms wallclock
  // so every particle is at least perceptible; the speed-encodes-
  // latency metaphor degrades gracefully toward "everything looks
  // fast" at extreme speeds, which is the right semantic.
  const MIN_LIFE_ANIM = 0.2;
  // Max simultaneous particles in flight. Spawning beyond this
  // drops oldest first to keep the canvas readable. 2000 puts the
  // draw cost at ~2 ms/frame on a typical laptop; well under the
  // 16 ms RAF budget.
  const MAX_PARTICLES = 2000;

  // FNV-1a 32-bit hash. Stable per-input, near-uniform output.
  // Used by the "spread" layout to give every IP a deterministic
  // angular slot independent of its numeric value or its rank,
  // so /24 neighbours don't cluster and the busiest IP doesn't
  // always land at 0°.
  function _hash32(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return h >>> 0;
  }

  function _hashAngleRad(s) {
    return (_hash32(s) * Math.PI * 2) / 0x100000000;
  }

  function IPFlowAnimator(canvas, options = {}) {
    const self = {
      canvas,
      options,
      ips: {},
      layout: "numeric",          // "numeric" | "spread"
      particles: [],
      pending: [],                 // sorted by t ascending
      seen: new Set(),             // (t.toFixed(3) + "|" + ip)
      mode: "live",                // "live" | "scrub" | "paused"
      speed: 1,
      playhead_t: 0,               // record-time epoch seconds
      playhead_anim_t: 0,          // performance.now()/1000 anchor
      _raf: null,
      _lastSpawnPurge: 0,
    };

    function setLayout(l) {
      self.layout = (l === "spread") ? "spread" : "numeric";
      _requestDraw();
    }

    function setIPs(ips) {
      self.ips = ips || {};
    }

    function _angleRad(ip) {
      if (self.layout === "spread") return _hashAngleRad(ip);
      const meta = self.ips[ip];
      if (!meta) return 0;
      return (meta.angle * Math.PI) / 180;
    }

    function effectivePlayhead() {
      if (self.mode === "paused") return self.playhead_t;
      const dt = performance.now() / 1000 - self.playhead_anim_t;
      return self.playhead_t + dt * self.speed;
    }

    function setLive(now_t) {
      self.mode = "live";
      self.speed = 1;
      // In live mode the playhead just sits at the newest data;
      // particle spawning is driven by addRecords append, not by
      // the playhead's forward motion. Keeping playhead pegged at
      // (typically) wallclock now means the timeline cursor sits
      // on the right edge.
      self.playhead_t = now_t || (Date.now() / 1000);
      self.playhead_anim_t = performance.now() / 1000;
      _requestDraw();
    }

    function startScrub(start_t, speed) {
      self.mode = "scrub";
      self.speed = Math.max(1, speed || 1);
      self.playhead_t = start_t;
      self.playhead_anim_t = performance.now() / 1000;
      _requestDraw();
    }

    function setSpeed(speed) {
      const ph = effectivePlayhead();
      self.speed = Math.max(1, +speed || 1);
      self.playhead_t = ph;
      self.playhead_anim_t = performance.now() / 1000;
      if (self.mode === "paused") return;
      _requestDraw();
    }

    function pause() {
      if (self.mode === "paused") return;
      self.playhead_t = effectivePlayhead();
      self.playhead_anim_t = performance.now() / 1000;
      self.mode = "paused";
      if (self._raf != null) {
        cancelAnimationFrame(self._raf);
        self._raf = null;
      }
      _requestDraw();
    }

    function resume() {
      if (self.mode !== "paused") return;
      self.mode = "scrub";
      self.playhead_anim_t = performance.now() / 1000;
      _requestDraw();
    }

    // Append records (sorted ascending by t) to the pending queue.
    // Dedup by (t.toFixed(3), ip). In live mode, addRecords spawns
    // the records immediately at "now"; in scrub mode they sit in
    // pending until the playhead crosses them.
    function addRecords(records, mode) {
      if (mode === "live") {
        const now = performance.now() / 1000;
        for (const r of records || []) {
          const k = r.t.toFixed(3) + "|" + r.ip;
          if (self.seen.has(k)) continue;
          self.seen.add(k);
          _spawnParticle(r, now);
        }
      } else {
        for (const r of records || []) {
          const k = r.t.toFixed(3) + "|" + r.ip;
          if (self.seen.has(k)) continue;
          self.seen.add(k);
          self.pending.push(r);
        }
        self.pending.sort((a, b) => a.t - b.t);
      }
      _requestDraw();
    }

    function clearReplay() {
      // Drop any in-flight scrub state so a new scrub starts fresh
      // (the seen-set is also cleared so the fetch can re-deliver
      // records from the new range without triggering dedup).
      self.particles = [];
      self.pending = [];
      self.seen.clear();
    }

    function _spawnParticle(rec, spawnAnimT) {
      const angleRad = _angleRad(rec.ip);
      const lifeRecord = (rec.dur_ms || 1) / 1000;
      const lifeAnim = Math.max(MIN_LIFE_ANIM,
                                lifeRecord / Math.max(1, self.speed));
      self.particles.push({
        spawnAt: spawnAnimT,
        life: lifeAnim,
        angleRad,
        radius: _radiusForBytes(rec.bytes || 0),
        color: _statusColor(rec.status || 0),
      });
      // Drop oldest if we've exceeded the cap.
      if (self.particles.length > MAX_PARTICLES) {
        self.particles.splice(0, self.particles.length - MAX_PARTICLES);
      }
    }

    function _purgeSeen(now) {
      // The seen-set grows unboundedly otherwise; trim every minute
      // by simply rebuilding from pending + a small recent window
      // around the playhead. The cost is acceptable up to a few
      // 100k entries; for longer playbacks we'd add expiry-by-time.
      if (now - self._lastSpawnPurge < 60) return;
      self._lastSpawnPurge = now;
      if (self.seen.size <= 200000) return;
      const fresh = new Set();
      for (const r of self.pending) {
        fresh.add(r.t.toFixed(3) + "|" + r.ip);
      }
      self.seen = fresh;
    }

    function _draw() {
      self._raf = null;
      const { ctx, w, h } = _setupHiDPI(self.canvas);
      ctx.fillStyle = PALETTE.bg;
      ctx.fillRect(0, 0, w, h);

      const cx = w / 2, cy = h / 2;
      const R = Math.max(40, Math.min(w, h) / 2 - 24);

      // Rim circle.
      ctx.strokeStyle = "rgba(155, 175, 198, 0.20)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.stroke();

      const ipList = Object.entries(self.ips)
        .sort((a, b) => b[1].count - a[1].count);

      // Tick mark at every IP's slot — subtle so they don't crowd
      // the rim, but visible so the operator sees the full distribution.
      ctx.fillStyle = "rgba(155, 175, 198, 0.30)";
      for (const [ip, meta] of ipList) {
        const a = self.layout === "spread"
                ? _hashAngleRad(ip)
                : (meta.angle * Math.PI) / 180;
        const x = cx + Math.cos(a) * R;
        const y = cy + Math.sin(a) * R;
        ctx.beginPath();
        ctx.arc(x, y, 1.3, 0, Math.PI * 2);
        ctx.fill();
      }

      // Top-N by count: bigger blue dot + IP/cc label.
      const HOT_N = 32;
      const hot = ipList.slice(0, HOT_N);
      ctx.font = "10px ui-monospace, monospace";
      ctx.textBaseline = "middle";
      for (const [ip, meta] of hot) {
        const a = self.layout === "spread"
                ? _hashAngleRad(ip)
                : (meta.angle * Math.PI) / 180;
        const x = cx + Math.cos(a) * R;
        const y = cy + Math.sin(a) * R;
        ctx.fillStyle = "#5dade2";
        ctx.beginPath();
        ctx.arc(x, y, 2.5, 0, Math.PI * 2);
        ctx.fill();
        const lx = cx + Math.cos(a) * (R + 8);
        const ly = cy + Math.sin(a) * (R + 8);
        ctx.fillStyle = "#cbd5e0";
        ctx.textAlign = (Math.cos(a) >= 0) ? "left" : "right";
        const label = meta.cc ? `${ip} ${meta.cc}` : ip;
        ctx.fillText(label, lx, ly);
      }

      // Spawn pending records the playhead has crossed.
      const now = performance.now() / 1000;
      const ph = effectivePlayhead();
      if (self.mode !== "paused") {
        let spawnedThisFrame = 0;
        while (self.pending.length && self.pending[0].t <= ph) {
          const r = self.pending.shift();
          _spawnParticle(r, now);
          spawnedThisFrame++;
          // Cap per-frame spawns so a giant pending queue with low
          // dur_ms doesn't blow the frame budget.
          if (spawnedThisFrame >= 500) break;
        }
      }

      // Particles.
      const live = [];
      for (const p of self.particles) {
        const elapsed = now - p.spawnAt;
        const progress = elapsed / p.life;
        if (progress > 1.05) continue;
        live.push(p);
        const r = R * (1 - Math.min(1, progress));
        const x = cx + Math.cos(p.angleRad) * r;
        const y = cy + Math.sin(p.angleRad) * r;
        ctx.fillStyle = p.color;
        ctx.globalAlpha = progress > 1 ? Math.max(0, 1 - (progress - 1) * 20) : 1;
        ctx.beginPath();
        ctx.arc(x, y, p.radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      self.particles = live;

      // Centre.
      ctx.fillStyle = "#3a4f70";
      ctx.beginPath();
      ctx.arc(cx, cy, 6, 0, Math.PI * 2);
      ctx.fill();

      // Stats overlay.
      ctx.fillStyle = PALETTE.axis;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.font = "11px ui-monospace, monospace";
      const ipCount = ipList.length;
      const partCount = self.particles.length;
      const phStr = new Date(ph * 1000).toTimeString().slice(0, 8);
      const speedStr = self.speed === 1 ? "1×" : `${self.speed}×`;
      const tag = self.mode === "paused"
                ? "paused"
                : (self.mode === "scrub"
                    ? `scrub ${phStr} ${speedStr}`
                    : "live");
      ctx.fillText(
        `${tag}  ips:${ipCount}  particles:${partCount}  pending:${self.pending.length}`,
        8, 8);

      _purgeSeen(now);

      // Notify the panel of the current playhead so it can move
      // the cursor div over each timeline chart.
      if (typeof options.onPlayhead === "function") {
        options.onPlayhead(ph);
      }

      // In live mode we always have the cursor walking visibly
      // (even when no new records arrive). In scrub mode we tick
      // until pending drains AND every particle expires. In paused
      // mode we don't tick at all.
      const wantFrame = (self.mode !== "paused")
        && (self.particles.length > 0
            || self.pending.length > 0
            || self.mode === "live"
            || self.mode === "scrub");
      if (wantFrame) _requestDraw();
    }

    function _requestDraw() {
      if (self._raf != null) return;
      self._raf = requestAnimationFrame(_draw);
    }

    function destroy() {
      if (self._raf != null) cancelAnimationFrame(self._raf);
      self._raf = null;
      self.particles = [];
      self.pending = [];
    }

    setLive();        // start in live mode at wallclock now
    _requestDraw();

    return {
      addRecords, setIPs, setLayout, setLive, startScrub,
      setSpeed, pause, resume, clearReplay, destroy,
      get mode() { return self.mode; },
      get speed() { return self.speed; },
      get playhead() { return effectivePlayhead(); },
      get pendingCount() { return self.pending.length; },
      get particleCount() { return self.particles.length; },
    };
  }

  // ---- legend ----------------------------------------------------

  function buildLegend(parent) {
    parent.innerHTML = "";
    const make = (cls, text) => {
      const s = document.createElement("span");
      s.className = cls;
      s.textContent = text;
      return s;
    };
    parent.appendChild(make("lg-key", "colour:"));
    for (const [cls, label] of [
      ["lg-2xx", "2xx"], ["lg-3xx", "3xx"],
      ["lg-4xx", "4xx"], ["lg-5xx", "5xx"],
    ]) {
      const dot = document.createElement("span");
      dot.className = "lg-dot " + cls;
      const w = document.createElement("span");
      w.className = "lg-swatch";
      w.appendChild(dot);
      w.appendChild(document.createTextNode(label));
      parent.appendChild(w);
    }
    parent.appendChild(make("lg-sep", "·"));
    parent.appendChild(make("lg-key", "speed ∝ 1 / latency"));
    parent.appendChild(make("lg-sep", "·"));
    parent.appendChild(make("lg-key", "radius ∝ log₁₀(bytes)"));
    parent.appendChild(make("lg-sep", "·"));
    parent.appendChild(make("lg-key", "angle: by IP"));
  }

  global.smIPFlow = {
    drawTimeline,
    attachTimelineClick: _attachTimelineClick,
    positionCursor,
    IPFlowAnimator,
    buildLegend,
  };
})(window);
