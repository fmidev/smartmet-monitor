// flame.js — interactive Canvas flame graph.
//
// Inputs are folded stacks: [{frames: ["main", "foo", "bar"], weight: N}].
// frames are root → leaf. Renders one rectangle per (path, depth);
// width proportional to its subtree weight.
//
// Interactions:
//   * click rectangle → zoom (rectangle becomes the new root)
//   * click breadcrumb segment → zoom out to that level
//   * mousemove → tooltip with full function name + count + %
//   * search box → highlight matching frames; non-matches grey out
//
// Coloring is deterministic per function name so the same symbol
// looks identical across modes (operators learn the colors of common
// hot paths over time).

(function (global) {
  "use strict";

  const ROW_H = 18;
  const PAD_L = 6;
  const PAD_T = 4;

  // ---- tree -------------------------------------------------------

  // Build a nested tree out of folded stacks. Each node is
  // { name, weight, children: Map<name, node> }.
  function buildTree(stacks) {
    const root = { name: "(root)", weight: 0, children: new Map() };
    for (const s of stacks) {
      const w = s.weight || 0;
      if (!w) continue;
      let node = root;
      node.weight += w;
      for (const name of s.frames) {
        let child = node.children.get(name);
        if (!child) {
          child = { name, weight: 0, children: new Map() };
          node.children.set(name, child);
        }
        child.weight += w;
        node = child;
      }
    }
    return root;
  }

  function pathTo(root, path) {
    let node = root;
    for (const seg of path) {
      const child = node.children.get(seg);
      if (!child) return null;
      node = child;
    }
    return node;
  }

  // ---- color ------------------------------------------------------

  // Hash the function name to a hue. Yellows/oranges for SmartMet
  // frames (so they pop), blues / greens for everything else.
  function colorFor(name) {
    let h = 0;
    for (let i = 0; i < name.length; i++) {
      h = ((h << 5) - h + name.charCodeAt(i)) | 0;
    }
    const isSmartMet = name.startsWith("SmartMet::") ||
                        name === "smartmetd";
    if (isSmartMet) {
      const hue = 25 + (Math.abs(h) % 35);   // 25..60° (orange→yellow)
      return `hsl(${hue}, 75%, 55%)`;
    }
    const hue = 180 + (Math.abs(h) % 90);    // 180..270° (cyan→violet)
    return `hsl(${hue}, 35%, 50%)`;
  }

  // ---- renderer ---------------------------------------------------

  class FlameView {
    constructor(canvas, tooltipEl) {
      this.canvas = canvas;
      this.tooltip = tooltipEl;
      this.ctx = canvas.getContext("2d");
      this.tree = null;
      this.totalWeight = 0;
      this.zoomPath = [];     // names from root → current zoom node
      this.search = "";
      this.frames = [];       // list of drawn rectangles for hit-test
      this.unit = "samples";

      this.canvas.addEventListener("click", e => this._onClick(e));
      this.canvas.addEventListener("mousemove", e => this._onMove(e));
      this.canvas.addEventListener("mouseleave", () => this._hideTip());

      this.onZoom = null;
    }

    setData(stacks, opts = {}) {
      this.tree = buildTree(stacks);
      this.totalWeight = this.tree.weight;
      this.unit = opts.unit || "samples";
      this.zoomPath = [];
      this.draw();
      if (this.onZoom) this.onZoom(this.zoomPath);
    }

    setSearch(term) {
      this.search = (term || "").toLowerCase();
      this.draw();
    }

    zoomTo(path) {
      this.zoomPath = path.slice();
      this.draw();
      if (this.onZoom) this.onZoom(this.zoomPath);
    }

    zoomOut() {
      if (this.zoomPath.length > 0) {
        this.zoomPath.pop();
        this.draw();
        if (this.onZoom) this.onZoom(this.zoomPath);
      }
    }

    draw() {
      const c = this.canvas;
      const dpr = global.devicePixelRatio || 1;
      const cssW = c.clientWidth;
      const cssH = c.clientHeight;
      c.width  = Math.round(cssW * dpr);
      c.height = Math.round(cssH * dpr);
      const ctx = this.ctx;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#0e1116";
      ctx.fillRect(0, 0, cssW, cssH);

      this.frames = [];
      if (!this.tree || !this.totalWeight) {
        ctx.fillStyle = "#7c8794";
        ctx.font = "12px ui-monospace, monospace";
        ctx.fillText("(no flame data)", PAD_L, 16);
        return;
      }

      const visibleRoot = pathTo(this.tree, this.zoomPath);
      if (!visibleRoot || !visibleRoot.weight) {
        ctx.fillStyle = "#7c8794";
        ctx.font = "12px ui-monospace, monospace";
        ctx.fillText("(empty subtree)", PAD_L, 16);
        return;
      }

      const innerW = cssW - 2 * PAD_L;
      this._drawNode(ctx, visibleRoot, this.zoomPath,
                     PAD_L, PAD_T, innerW,
                     visibleRoot.weight);
    }

    _drawNode(ctx, node, path, x, y, w, parentWeight) {
      const cssH = this.canvas.clientHeight;
      if (y + ROW_H > cssH || w < 1) return;

      // Sort children largest-first so big rectangles are on the left.
      const children = Array.from(node.children.values())
        .sort((a, b) => b.weight - a.weight);
      let cx = x;
      for (const ch of children) {
        const cw = (ch.weight / parentWeight) * w;
        if (cw < 0.5) { cx += cw; continue; }
        const matches = !this.search ||
                        ch.name.toLowerCase().includes(this.search);
        const fill = colorFor(ch.name);
        ctx.fillStyle = matches ? fill : "rgba(60,70,85,0.45)";
        ctx.fillRect(cx, y, cw - 0.5, ROW_H - 1);
        if (cw > 30) {
          ctx.fillStyle = matches ? "#0e1116" : "#7c8794";
          ctx.font = "11px ui-monospace, monospace";
          ctx.textBaseline = "middle";
          ctx.textAlign = "left";
          const label = clipText(ctx, ch.name, cw - 6);
          ctx.fillText(label, cx + 3, y + ROW_H / 2);
        }
        const childPath = path.concat([ch.name]);
        this.frames.push({
          x: cx, y, w: cw, h: ROW_H,
          name: ch.name, weight: ch.weight,
          path: childPath,
        });
        this._drawNode(ctx, ch, childPath, cx, y + ROW_H, cw, ch.weight);
        cx += cw;
      }
    }

    _hit(e) {
      const rect = this.canvas.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      // Walk most-recently-added (which are deepest) first so a child
      // beats its parent on overlap. Frames are appended depth-first
      // so reversing approximates that.
      for (let i = this.frames.length - 1; i >= 0; i--) {
        const f = this.frames[i];
        if (px >= f.x && px <= f.x + f.w &&
            py >= f.y && py <= f.y + f.h) {
          return f;
        }
      }
      return null;
    }

    _onClick(e) {
      const f = this._hit(e);
      if (f) this.zoomTo(f.path);
    }

    _onMove(e) {
      const f = this._hit(e);
      if (!f) { this._hideTip(); return; }
      const total = this.totalWeight || 1;
      const pct = (f.weight / total * 100).toFixed(2);
      const w = formatWeight(f.weight, this.unit);
      this.tooltip.textContent = `${f.name} — ${w} (${pct}%)`;
      this.tooltip.classList.remove("hidden");
      const x = e.clientX + 14;
      const y = e.clientY + 14;
      this.tooltip.style.left = x + "px";
      this.tooltip.style.top  = y + "px";
    }

    _hideTip() {
      this.tooltip.classList.add("hidden");
    }
  }

  // ---- helpers ----------------------------------------------------

  function clipText(ctx, text, maxPx) {
    if (ctx.measureText(text).width <= maxPx) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      const candidate = text.slice(0, mid) + "…";
      if (ctx.measureText(candidate).width <= maxPx) lo = mid;
      else hi = mid - 1;
    }
    return lo > 0 ? text.slice(0, lo) + "…" : "";
  }

  function formatWeight(w, unit) {
    if (unit === "microseconds") {
      if (w < 1000) return w + "µs";
      if (w < 1e6)  return (w / 1000).toFixed(1) + "ms";
      return (w / 1e6).toFixed(1) + "s";
    }
    if (unit === "bytes") {
      const u = ["B", "K", "M", "G", "T"];
      let v = w, i = 0;
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      return v >= 100 ? `${v.toFixed(0)}${u[i]}` : `${v.toFixed(1)}${u[i]}`;
    }
    return String(w);
  }

  global.FlameView = FlameView;
})(window);
