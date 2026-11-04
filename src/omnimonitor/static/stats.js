/* OmniMonitor stats page. No external requests; data from ./api/stats only. */
(function () {
  "use strict";
  var data = null, mode = "daily", days = 30;
  if (window.OMApp) OMApp.shell("dashboard");
  document.getElementById("atabs").addEventListener("click", function (ev) {
    var b = ev.target.closest("button"); if (!b) return;
    days = parseInt(b.getAttribute("data-days"), 10) || 30;
    Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) { x.classList.toggle("on", x === b); });
    if (data) OMApp.renderActivity(data, days);
  });
  var $ = function (id) { return document.getElementById(id); };

  var fmtTokens = OM.fmtTokens, fmtDate = OM.fmtDate, el = OM.el, fmtUsd = OM.fmtUsd;
  var range = null;
  function fmtDur(s) {
    if (s === null || s === undefined) return "–";
    s = Math.round(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + x + "s";
    return x + "s";
  }
  function setStat(id, metric, fmt) {
    var node = $(id);
    node.textContent = "";
    if (!metric || metric.value === null || metric.value === undefined) {
      var na = el("span", "na", "not available");
      na.title = (metric && metric.reason) || "not available";
      node.appendChild(na);
    } else {
      node.textContent = fmt(metric.value);
    }
  }
  function color(pct) { return pct >= 50 ? "var(--ok)" : pct >= 20 ? "var(--warn)" : "var(--bad)"; }

  function render() {
    var d = data, st = d.status || {}, u = (st.usage) || null;
    var b = $("banners"); b.textContent = "";
    if (d.is_demo || (u && u.is_demo)) {
      b.appendChild(el("div", "banner demo",
        "DEMO MODE - every number on this page is fake sample data (including tools, projects and prices). Open Setup and click \"Use my real Grok usage\" (or run `omnimonitor setup`) to use your Grok account."));
    }
    if (st.error) b.appendChild(el("div", "banner err", "Last refresh failed" +
      (st.last_attempt ? " (" + fmtDate(st.last_attempt) + ")" : "") + ": " + st.error +
      (u ? " (showing the last good value)" : "") +
      (/grok login/.test(st.error) ? " - open Terminal and run: grok login (or ~/.grok/bin/grok login)" : "")));
    $("sub").textContent = "Updated " + (st.last_ok ? fmtDate(st.last_ok) : "never") +
      " · source: " + (st.source || "?") + " · tokens: " + (d.provider || "?") +
      (d.data_since ? " · token data since " + fmtDate(d.data_since) : "");

    if (u) {
      var rem = Math.round(u.remaining_percent);
      $("remaining").textContent = rem + "%";
      $("barfill").style.width = rem + "%";
      $("barfill").style.background = color(rem);
      $("reset").textContent = u.resets_at ? "Resets " + (OM.fmtCountdown(u.resets_at) ? OM.fmtCountdown(u.resets_at) + " · " : "") + fmtDate(u.resets_at) : "Reset time not reported";
      var parts = (u.products || []).map(function (p) { return p.product + " " + Math.round(p.used_percent) + "% used"; });
      if (u.raw_used_percent > 100) parts.push("reported " + u.raw_used_percent.toFixed(1) + "% (over pool)");
      $("products").textContent = parts.join(" · ");
    } else {
      $("remaining").textContent = "–";
      $("reset").textContent = st.error || "No data yet";
    }

    var wk = d.week_tokens || {};
    $("weeklabel").textContent = "Tokens used " + (wk.window ? "(" + wk.window + ")" : "this week");
    setStat("weektokens", wk, fmtTokens);
    var wl = d.weekly_limit || {}, wlNode = $("weeklimit"); wlNode.textContent = "";
    if (wl.value) {
      wlNode.textContent = "out of " + fmtTokens(wl.value) + " (" + wl.source + ")";
    } else if (wl.estimate) {
      var s = el("span", "est", "out of ≈" + fmtTokens(wl.estimate.value) + " tokens (estimate, derived from " +
        wl.estimate.basis + " % used)");
      s.title = wl.estimate.caveat; wlNode.appendChild(s);
    } else {
      var na = el("span", "na", "weekly token limit: not available");
      na.title = (wl.reason || "") + (wl.estimate_unavailable ? " Estimate: " + wl.estimate_unavailable + "." : "");
      wlNode.appendChild(na);
    }

    setStat("s-life", d.lifetime_tokens, fmtTokens);
    setStat("s-peak", d.peak_day, fmtTokens);
    $("s-peak-k").textContent = "Peak tokens" + (d.peak_day && d.peak_day.date ? " · " + fmtDate(d.peak_day.date) : " (1 day)");
    setStat("s-task", d.longest_task, fmtDur);
    $("s-task-k").textContent = "Longest task";
    if (d.longest_task && d.longest_task.source === "derived from log timestamps") {
      $("s-task-k").title = "Derived from log timestamps (first event of a turn to turn completion)";
      $("s-task-k").textContent = "Longest task (from log times)";
    }
    setStat("s-long", d.longest_streak, function (v) { return v + (v === 1 ? " day" : " days"); });
    setStat("s-cur", d.current_streak, function (v) { return v + (v === 1 ? " day" : " days"); });

    renderHeat();
    if (window.OMApp) OMApp.renderActivity(d, days);
    renderForecast(d);
    renderBreakdown(d);
    renderCost(d);

    var mc = $("models"); mc.textContent = "";
    (d.top_models || []).forEach(function (m) {
      var r = el("div", "row"); r.appendChild(el("span", "l", m.name));
      r.appendChild(el("span", "r", fmtTokens(m.tokens) + " · " + Math.round(m.share * 100) + "%"));
      mc.appendChild(r);
    });
    $("models-card").hidden = !(d.top_models || []).length;
    var pc = $("plugins"); pc.textContent = "";
    (d.top_plugins || []).forEach(function (p) {
      var r = el("div", "row"); r.appendChild(el("span", "l", p.name));
      r.appendChild(el("span", "r", p.value)); pc.appendChild(r);
    });
    $("plugins-card").hidden = !(d.top_plugins || []).length;
    var ic = $("insights"); ic.textContent = "";
    (d.insights || []).forEach(function (i) {
      var r = el("div", "row"); r.appendChild(el("span", "l", i.label));
      r.appendChild(el("span", "r", i.value)); ic.appendChild(r);
    });
    $("insights-card").hidden = !(d.insights || []).length;
    if (window.OMProviders) OMProviders.render(d);
    applyPersonal(d.personal || {});
  }

  function applyPersonal(p) {
    OM.applyTheme(p);
    if (p.display_name) {
      $("title").textContent = p.display_name;
      $("title-sub").textContent = "· OmniMonitor";
      document.title = p.display_name + " · OmniMonitor";
    }
    var hidden = p.hidden_cards || [];
    Array.prototype.forEach.call(document.querySelectorAll("[data-card]"), function (n) {
      if (hidden.indexOf(n.getAttribute("data-card")) >= 0) n.hidden = true;
    });
  }

  function setMini(id, value, reason) {
    var n = $(id); n.textContent = "";
    if (value === null || value === undefined) n.appendChild(OM.na(reason));
    else n.textContent = value;
  }

  function renderForecast(d) {
    var f = d.forecast || {}, a = f.average || {}, r = f.recent || {};
    $("fc-kind").textContent = f.is_demo ? "· DEMO" : "· estimates, linear";
    var st = $("fc-status"); st.textContent = "";
    if (a.status) {
      var cls = a.status === "ahead" ? (a.runs_out_at ? "bad" : "warn") : "ok";
      var pill = el("span", "pill " + cls, a.status === "ahead" ? "Ahead of pace" :
        a.status === "behind" ? "Under pace" : "On pace");
      pill.title = Math.round(f.used_percent) + "% used vs " + Math.round(a.even_pace_percent) +
        "% of the week elapsed";
      st.appendChild(pill);
    } else st.appendChild(OM.na(a.reason));
    setMini("fc-reset", a.percent_at_reset !== undefined ? "≈" + Math.round(Math.min(a.percent_at_reset, 999)) + "%" : null, a.reason);
    if (a.percent_at_reset !== undefined) {
      setMini("fc-out", a.runs_out_at ? fmtDate(a.runs_out_at) : "not before reset", "");
    } else setMini("fc-out", null, a.reason);
    setMini("fc-recent", r.percent_at_reset !== undefined ?
      (r.rate_per_hour * 24).toFixed(1) + "%/day → ≈" + Math.round(Math.min(r.percent_at_reset, 999)) + "%" : null, r.reason);
    var b = d.burn;
    var why = (d.lifetime_tokens || {}).reason || "no token data";
    setMini("b-hour", b ? fmtTokens(b.last_hour) : null, why);
    setMini("b-today", b ? fmtTokens(b.today) : null, why);
    setMini("b-rate", b && b.per_active_hour_today !== null ? fmtTokens(b.per_active_hour_today) : null,
      b ? "no activity today" : why);
    setMini("b-tools", (d.tools || []).length ? d.tools.map(function (t) { return t.name; }).join(", ") : null,
      "no token sources enabled");
  }

  function bRows(node, rows) {
    node.textContent = "";
    if (!rows.length) { node.appendChild(OM.na("nothing recorded in this range")); return; }
    var max = rows[0].tokens || 1;
    rows.forEach(function (r) {
      var row = el("div", "brow"), t = el("div", "t");
      var n = el("span", "n", r.name); n.title = r.name; t.appendChild(n);
      t.appendChild(el("span", "m", fmtTokens(r.tokens) + " · " + Math.round(r.share * 100) + "%"));
      row.appendChild(t);
      var m = el("div", "meter"), i = el("i"); i.style.width = Math.max(2, 100 * r.tokens / max) + "%";
      m.appendChild(i); row.appendChild(m);
      var bits = [r.sessions + (r.sessions === 1 ? " session" : " sessions")];
      if (r.cache_hit !== null) bits.push(Math.round(100 * r.cache_hit) + "% cached");
      if (r.estimated_usd !== null) bits.push("≈" + fmtUsd(r.estimated_usd) + " est.");
      else if (r.recorded_usd !== null) bits.push(fmtUsd(r.recorded_usd) + " recorded");
      row.appendChild(el("div", "d", bits.join(" · ")));
      node.appendChild(row);
    });
  }

  function renderBreakdown(d) {
    var br = d.breakdowns || {}, ids = ["today", "week", "7d", "30d", "all"].filter(function (k) { return br[k]; });
    $("breakdown-card").hidden = !ids.length;
    if (!ids.length) return;
    if (!range || !br[range]) range = br.week ? "week" : "7d";
    var tabs = $("rtabs"); tabs.textContent = "";
    var short = { today: "Today", week: "Billing week", "7d": "7d", "30d": "30d", all: "All" };
    ids.forEach(function (k) {
      var b = el("button", k === range ? "on" : "", short[k]); b.setAttribute("data-range", k);
      b.title = br[k].label; tabs.appendChild(b);
    });
    var cur = br[range];
    bRows($("bd-tools"), cur.tools); bRows($("bd-models"), cur.models); bRows($("bd-projects"), cur.projects);
  }

  function renderCost(d) {
    var c = d.cost || {}, rs = c.ranges || {}, ids = ["today", "week", "30d", "all"].filter(function (k) { return rs[k]; });
    $("cost-card").hidden = !ids.length;
    $("cost-note").textContent = c.note ? "· " + c.note : "";
    var g = $("cost-grid"); g.textContent = "";
    ids.forEach(function (k) {
      var r = rs[k], box = el("div");
      box.appendChild(el("div", "mini-k", r.label));
      var v = el("div", "mini-v");
      if (r.estimated_usd !== null) { v.textContent = "≈" + fmtUsd(r.estimated_usd); v.classList.add("est"); }
      else v.appendChild(OM.na(c.priced_models ? "no model in this range matches your price table" :
        "set prices in Settings to estimate cost", "no estimate"));
      box.appendChild(v);
      var sub = [];
      if (r.recorded_usd !== null) sub.push(fmtUsd(r.recorded_usd) + " recorded in logs");
      if (r.unpriced_tokens) sub.push(fmtTokens(r.unpriced_tokens) + " tokens unpriced");
      box.appendChild(el("div", "mini-k", sub.join(" · ")));
      if (r.unpriced_models && r.unpriced_models.length) box.lastChild.title = "Unpriced: " + r.unpriced_models.join(", ");
      g.appendChild(box);
    });
  }

  document.getElementById("rtabs").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button"); if (!btn) return;
    range = btn.getAttribute("data-range");
    if (data) renderBreakdown(data);
  });

  function renderHeat() {
    var cells = (data.heatmap || []).slice(), heat = $("heat"), months = $("months");
    heat.textContent = ""; months.textContent = "";
    var vals = cells.map(function (c) { return c.tokens; });
    if (mode === "weekly") {
      for (var i = 0; i < cells.length; i += 7) {
        var sum = 0; for (var j = i; j < Math.min(i + 7, cells.length); j++) sum += cells[j].tokens;
        for (j = i; j < Math.min(i + 7, cells.length); j++) vals[j] = sum;
      }
    } else if (mode === "cumulative") {
      var run = 0; vals = cells.map(function (c) { run += c.tokens; return run; });
    }
    var nz = vals.filter(function (v) { return v > 0; }).sort(function (a, b) { return a - b; });
    function q(p) { return nz.length ? nz[Math.min(nz.length - 1, Math.floor(p * nz.length))] : 0; }
    var t1 = q(0.25), t2 = q(0.5), t3 = q(0.8);
    var lastMonth = -1;
    cells.forEach(function (c, idx) {
      var v = vals[idx], lvl = v <= 0 ? 0 : v <= t1 ? 1 : v <= t2 ? 2 : v <= t3 ? 3 : 4;
      var i = el("i"); i.style.background = "var(--g" + lvl + ")";
      i.title = fmtDate(c.date) + ": " + fmtTokens(c.tokens) + " tokens" +
        (mode === "daily" ? "" : " (" + mode + " " + fmtTokens(v) + ")");
      heat.appendChild(i);
      if (idx % 7 === 0) {
        var m = new Date(c.date + "T12:00:00").getMonth();
        var s = el("span", null, m !== lastMonth ? new Date(c.date + "T12:00:00").toLocaleString(undefined, { month: "short" }) : "");
        months.appendChild(s); lastMonth = m;
      }
    });
  }

  document.getElementById("tabs").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button"); if (!btn) return;
    mode = btn.getAttribute("data-mode");
    Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) {
      b.classList.toggle("on", b === btn);
    });
    if (data) renderHeat();
  });

  function load() {
    fetch("api/stats", { cache: "no-store" }).then(function (r) { return r.json(); })
      .then(function (d) { data = d; render(); })
      .catch(function (e) { $("sub").textContent = "Could not load stats: " + e; });
  }
  load();
  setInterval(load, 60000);
})();
