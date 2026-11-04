/* OmniMonitor app shell: sidebar navigation + small SVG charts. No external requests,
   no libraries; every number comes from ./api/stats. Exposes window.OMApp. */
(function () {
  "use strict";
  var el = OM.el, SVG = "http://www.w3.org/2000/svg";
  var NAV = [
    ["dashboard", "./#top", "Dashboard"],
    ["providers", "./#providers-card", "Providers"],
    ["activity", "./#activity-card", "Activity"],
    ["breakdown", "./#breakdown-card", "Breakdown"],
    ["settings", "settings", "Settings"],
    ["mini", "mini", "Mini view"]
  ];
  var CSS =
    "body.app{display:flex;min-height:100vh}" +
    ".side{width:200px;flex:0 0 200px;position:sticky;top:0;height:100vh;padding:22px 12px;" +
    "border-right:1px solid var(--line);background:var(--card);display:flex;flex-direction:column;gap:2px}" +
    ".side .brand{font-weight:650;font-size:15px;padding:0 10px 14px}" +
    ".side .brand small{display:block;color:var(--mut);font-weight:400;font-size:11.5px}" +
    ".side a{color:var(--fg);text-decoration:none;padding:7px 10px;border-radius:8px;font-size:13.5px}" +
    ".side a:hover{background:var(--chip)}.side a.on{background:var(--chipon);font-weight:600}" +
    ".side .sp{flex:1}.side .ft{color:var(--mut);font-size:11px;padding:8px 10px}" +
    "body.app main{flex:1;max-width:1100px}" +
    "body.app nav.links a[href='settings'],body.app nav.links a[href='mini'],body.app nav.links a[href='./']{display:none}" +
    ".chart svg{width:100%;height:auto;display:block}.chart .ax{fill:var(--mut);font-size:10px}" +
    ".chart rect.b{fill:var(--accent)}.chart rect.b:hover{fill:var(--g4)}" +
    ".legend2{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--mut);margin-top:8px}" +
    ".legend2 i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}" +
    "@media (max-width:760px){.side{display:none}}";

  function shell(active) {
    var st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
    document.body.classList.add("app");
    var side = el("aside", "side"), b = el("div", "brand", "OmniMonitor");
    b.appendChild(el("small", null, "AI usage monitor"));
    side.appendChild(b);
    NAV.forEach(function (n) {
      var a = el("a", n[0] === active ? "on" : "", n[2]); a.href = n[1];
      a.setAttribute("data-nav", n[0]); side.appendChild(a);
    });
    side.appendChild(el("div", "sp"));
    side.appendChild(el("div", "ft", "Local only · 127.0.0.1"));
    document.body.insertBefore(side, document.body.firstChild);
    side.addEventListener("click", function (ev) {
      var a = ev.target.closest("a[data-nav]"); if (!a) return;
      Array.prototype.forEach.call(side.querySelectorAll("a"), function (x) { x.classList.remove("on"); });
      a.classList.add("on");
    });
  }

  function svg(tag, attrs) {
    var n = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }

  /* Daily tokens bar chart. points: [{date, tokens}] */
  function barChart(node, points) {
    node.textContent = "";
    if (!points.length || !points.some(function (p) { return p.tokens > 0; })) {
      node.appendChild(OM.na("no token activity recorded in this range", "no activity in this range"));
      return;
    }
    var W = 720, H = 180, pad = 26, n = points.length, bw = (W - pad) / n;
    var max = Math.max.apply(null, points.map(function (p) { return p.tokens; })) || 1;
    var s = svg("svg", { viewBox: "0 0 " + W + " " + (H + 18), role: "img",
                          "aria-label": "Tokens per day" });
    [0.5, 1].forEach(function (f) {
      var y = H - (H - 10) * f;
      s.appendChild(svg("line", { x1: pad, x2: W, y1: y, y2: y, stroke: "var(--line)" }));
      var t = svg("text", { x: 0, y: y + 3, "class": "ax" }); t.textContent = OM.fmtTokens(max * f);
      s.appendChild(t);
    });
    points.forEach(function (p, i) {
      var h = p.tokens > 0 ? Math.max(1.5, (H - 10) * p.tokens / max) : 0;
      var r = svg("rect", { x: pad + i * bw + 1, y: H - h, width: Math.max(1, bw - 2), height: h,
                            rx: 2, "class": "b" });
      var tt = svg("title"); tt.textContent = OM.fmtDate(p.date) + ": " + OM.fmtTokens(p.tokens) + " tokens";
      r.appendChild(tt); s.appendChild(r);
      if (i === 0 || i === n - 1 || i === Math.floor(n / 2)) {
        var t = svg("text", { x: pad + i * bw + bw / 2, y: H + 14, "class": "ax", "text-anchor": "middle" });
        t.textContent = p.date.slice(5); s.appendChild(t);
      }
    });
    node.appendChild(s);
  }

  /* Share bar (horizontal stacked) for rows: [{name, tokens}] */
  var PALETTE = ["#2a8bd8", "#3fb950", "#d29922", "#a371f7", "#f85149", "#39c5cf", "#db61a2", "#8b949e"];
  function shareChart(node, rows) {
    node.textContent = "";
    rows = (rows || []).filter(function (r) { return r.tokens > 0; });
    if (!rows.length) { node.appendChild(OM.na("nothing recorded in this range")); return; }
    var top = rows.slice(0, 7), rest = rows.slice(7).reduce(function (a, r) { return a + r.tokens; }, 0);
    if (rest) top.push({ name: "other", tokens: rest });
    var total = top.reduce(function (a, r) { return a + r.tokens; }, 0);
    var W = 720, x = 0, s = svg("svg", { viewBox: "0 0 " + W + " 22", role: "img", "aria-label": "Share by tool" });
    var lg = el("div", "legend2");
    top.forEach(function (r, i) {
      var w = W * r.tokens / total, c = PALETTE[i % PALETTE.length];
      var rect = svg("rect", { x: x, y: 0, width: Math.max(0.5, w), height: 22, fill: c });
      var tt = svg("title"); tt.textContent = r.name + ": " + OM.fmtTokens(r.tokens); rect.appendChild(tt);
      s.appendChild(rect); x += w;
      var item = el("span"), sw = el("i"); sw.style.background = c; item.appendChild(sw);
      item.appendChild(document.createTextNode(r.name + " " + Math.round(100 * r.tokens / total) + "%"));
      lg.appendChild(item);
    });
    node.appendChild(s); node.appendChild(lg);
  }

  function renderActivity(d, days) {
    var cells = (d.heatmap || []).slice(-(days || 30));
    var c = document.getElementById("chart-daily");
    if (c) barChart(c, cells);
    var br = (d.breakdowns || {})["30d"] || (d.breakdowns || {})["7d"];
    var s = document.getElementById("chart-tools");
    if (s) shareChart(s, br ? br.tools : []);
  }

  window.OMApp = { shell: shell, barChart: barChart, shareChart: shareChart,
                   renderActivity: renderActivity };
})();
