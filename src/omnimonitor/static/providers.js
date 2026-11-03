/* OmniMonitor providers section: all tools, budgets, per-provider cards/tabs.
   Data from ./api/stats (field "providers") only; no external requests. */
(function () {
  "use strict";
  var el = OM.el, fmtTokens = OM.fmtTokens, fmtUsd = OM.fmtUsd;
  var tab = "all", last = null;
  function $(id) { return document.getElementById(id); }
  function fmtTs(ts) { return ts ? OM.fmtDate(new Date(ts * 1000).toISOString()) : ""; }
  function usd(v, reason) { return v === null || v === undefined ? OM.na(reason || "no recorded cost and no matching price") : document.createTextNode(fmtUsd(v)); }
  function pillFor(status) {
    var cls = { ok: "ok", high: "warn", warn: "warn", over: "bad" }[status] || "ok";
    var txt = { ok: "on pace", high: "ahead of pace", warn: "near budget", over: "on pace to exceed" }[status] || status;
    return el("span", "pill " + cls, txt);
  }
  function kv(k, vNode) {
    var d = el("div"); d.appendChild(el("div", "mini-k", k));
    var v = el("div", "mini-v"); v.appendChild(typeof vNode === "string" ? document.createTextNode(vNode) : vNode);
    d.appendChild(v); return d;
  }
  function meter(pct, color) {
    var b = el("div", "bar"), i = el("i");
    i.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
    i.style.background = color || "var(--accent)";
    b.appendChild(i); return b;
  }
  function usedColor(p) { return p >= 90 ? "var(--bad)" : p >= 70 ? "var(--warn)" : "var(--ok)"; }

  function renderAll(ov) {
    var g = $("pv-all"); g.textContent = "";
    var at = ov.all_tools || {};
    [["today", "Today"], ["7d", "Last 7 days"], ["30d", "Last 30 days"]].forEach(function (r) {
      var x = at[r[0]] || {}, d = el("div");
      d.appendChild(el("div", "mini-k", r[1] + " · all tools"));
      d.appendChild(el("div", "mini-v", fmtTokens(x.tokens || 0) + " tok"));
      var c = el("div", "sub"); c.appendChild(x.tokens ? usd(x.usd) : document.createTextNode("–"));
      if (x.unpriced_tokens && x.usd !== null && x.usd !== undefined) c.appendChild(document.createTextNode(" + " + fmtTokens(x.unpriced_tokens) + " unpriced"));
      d.appendChild(c); g.appendChild(d);
    });
    var cc = ov.catalog_counts || {}, d2 = el("div");
    d2.appendChild(el("div", "mini-k", "Provider catalog"));
    d2.appendChild(el("div", "mini-v", (cc.supported || 0) + " supported"));
    d2.appendChild(el("div", "sub", (cc.partial || 0) + " partial · " + (cc.not_supported || 0) + " not supported (see docs/providers.md)"));
    g.appendChild(d2);
  }

  function renderBudgets(ov) {
    var b = ov.budgets || {}, g = $("pv-budgets"); g.textContent = "";
    var names = { daily: "Today", weekly: "This week", monthly: "This month" }, any = false;
    ["daily", "weekly", "monthly"].forEach(function (p) {
      var r = (b.periods || {})[p]; if (!r) return;
      var d = el("div");
      d.appendChild(el("div", "mini-k", names[p] + (r.limit_usd ? " · budget " + fmtUsd(r.limit_usd) : " · no budget set")));
      var v = el("div", "mini-v"); v.appendChild(usd(r.spent_usd, r.note));
      if (r.percent !== null && r.percent !== undefined) {
        any = true;
        v.appendChild(document.createTextNode(" "));
        v.appendChild(pillFor(r.state === "ok" ? "ok" : r.state));
      }
      d.appendChild(v);
      if (r.percent !== null && r.percent !== undefined) d.appendChild(meter(r.percent, usedColor(r.percent)));
      d.appendChild(el("div", "sub", (r.note ? r.note + " · " : "") + "resets " + fmtTs(r.resets_at)));
      g.appendChild(d);
    });
    $("pv-budget-note").textContent = (b.basis || "") + (any ? "" : " · set budgets in Settings");
  }

  function limitRow(w) {
    var d = el("div", "brow"), t = el("div", "t");
    t.appendChild(el("span", "n", w.name || "limit"));
    var used = w.used_percent;
    t.appendChild(el("span", "m", used === null || used === undefined ? "not available" :
      Math.round(used) + "% used" + (w.stale ? " (stale)" : "")));
    d.appendChild(t);
    if (used !== null && used !== undefined) {
      var m = el("div", "meter"), i = el("i"); i.style.width = Math.min(100, used) + "%";
      i.style.background = usedColor(used); m.appendChild(i); d.appendChild(m);
    }
    var bits = [];
    if (w.resets_at) bits.push("resets " + fmtTs(w.resets_at));
    if (w.pace) {
      bits.push("≈" + Math.round(w.pace.projected_percent) + "% at reset (estimate, " + Math.round(w.pace.elapsed_percent) + "% of window gone)");
      if (w.pace.runs_out_at) bits.push("≈ runs out " + fmtTs(w.pace.runs_out_at));
    }
    d.appendChild(el("div", "d", bits.join(" · ")));
    if (w.pace) { var p = pillFor(w.pace.status); p.style.marginTop = "4px"; d.appendChild(p); }
    return d;
  }

  function card(c, full) {
    var box = el("div", "card pv-card");
    box.setAttribute("data-provider", c.id);
    var h = el("div", "t"); h.style.display = "flex"; h.style.justifyContent = "space-between";
    h.appendChild(el("strong", "", c.name));
    h.appendChild(el("span", "sub", (c.demo ? "DEMO · " : "") + ({ local_logs: "local logs", api: "account API", billing: "Grok billing" }[c.kind] || c.kind)));
    box.appendChild(h);
    if (c.error) box.appendChild(el("div", "sub est", "⚠ " + c.error));
    (c.limits || []).forEach(function (w) { box.appendChild(limitRow(w)); });
    if (c.ranges) {
      var g = el("div", "grid3"); g.style.marginTop = "8px";
      [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"]].forEach(function (r) {
        var x = c.ranges[r[0]] || {}, d = el("div");
        d.appendChild(el("div", "mini-k", r[1]));
        d.appendChild(el("div", "", fmtTokens(x.tokens || 0) + " tok"));
        var s = el("div", "sub"); s.appendChild(x.tokens ? usd(x.usd) : document.createTextNode("–")); d.appendChild(s);
        g.appendChild(d);
      });
      box.appendChild(g);
    }
    (c.metrics || []).forEach(function (m) {
      var d = el("div", "brow"), t = el("div", "t");
      t.appendChild(el("span", "n", m.label));
      t.appendChild(el("span", "m", m.unit === "USD" ? fmtUsd(m.value) : (m.value + (m.unit ? " " + m.unit : ""))));
      d.appendChild(t); box.appendChild(d);
    });
    if (full) {
      if ((c.top_models || []).length) {
        box.appendChild(el("h3", "", "Top models (30 days)"));
        c.top_models.forEach(function (m) {
          var d = el("div", "brow"), t = el("div", "t");
          t.appendChild(el("span", "n", m.model)); t.appendChild(el("span", "m", fmtTokens(m.tokens)));
          d.appendChild(t); box.appendChild(d);
        });
      }
      var meta = [];
      if (c.plan_type) meta.push("plan: " + c.plan_type);
      if (c.period) meta.push(c.period);
      if (c.reset) meta.push("reset: " + c.reset);
      if (c.confidence) meta.push("confidence: " + c.confidence);
      if (c.fetched_at) meta.push("fetched " + fmtTs(c.fetched_at));
      if (c.last_seen) meta.push("last activity " + fmtTs(c.last_seen));
      if (c.note) meta.push(c.note);
      box.appendChild(el("div", "sub", meta.join(" · ")));
      (c.docs || []).forEach(function (u) {
        if (/^https:\/\//.test(u)) {
          var a = el("a", "", u); a.href = u; a.rel = "noopener noreferrer"; a.target = "_blank";
          var dd = el("div", "sub"); dd.appendChild(a); box.appendChild(dd);
        } else box.appendChild(el("div", "sub", u));
      });
    } else if (c.last_seen) {
      box.appendChild(el("div", "sub", "last activity " + fmtTs(c.last_seen)));
    }
    return box;
  }

  function renderCards(ov) {
    var tabs = $("pv-tabs"), grid = $("pv-cards"), cards = ov.cards || [];
    if (tab !== "all" && !cards.some(function (c) { return c.id === tab; })) tab = "all";
    tabs.textContent = "";
    var fam = {};
    cards.forEach(function (c) { fam[c.family] = (fam[c.family] || 0) + 1; });
    [{ id: "all", name: "All" }].concat(cards).forEach(function (c) {
      var lab = c.id === "all" ? "All" : (fam[c.family] > 1 || !c.family ? c.name.replace(/ \(.*\)$/, "") : c.family);
      var b = el("button", c.id === tab ? "on" : "", lab);
      b.title = c.name; b.setAttribute("data-pv", c.id); tabs.appendChild(b);
    });
    grid.textContent = "";
    grid.className = tab === "all" ? "pv-grid" : "";
    cards.filter(function (c) { return tab === "all" || c.id === tab; })
      .forEach(function (c) { grid.appendChild(card(c, tab !== "all")); });
    if (!cards.length) grid.appendChild(el("div", "sub", "No providers enabled. Enable some in Settings or with `omnimonitor providers enable <id>`."));
  }

  function render(d) {
    var ov = d && d.providers, sec = $("providers-card");
    if (!sec) return;
    if (!ov || ov.error || !ov.cards) {
      sec.hidden = !ov;
      if (ov && ov.error) { $("pv-cards").textContent = "Providers overview failed: " + ov.error; }
      return;
    }
    last = ov; sec.hidden = false; $("budgets-card").hidden = false;
    var p = ov.primary || {};
    $("pv-primary").textContent = p.available ? "Tray follows: " + p.label + " (" + Math.round(p.used_percent) + "% used)"
      : (p.name ? "Tray follows " + p.name + ": not available - " + (p.reason || "") + " (falls back to the Grok pool)" : "");
    renderAll(ov); renderBudgets(ov); renderCards(ov);
  }
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("#pv-tabs button");
    if (!b) return;
    tab = b.getAttribute("data-pv"); if (last) renderCards(last);
  });
  window.OMProviders = { render: render };
})();
