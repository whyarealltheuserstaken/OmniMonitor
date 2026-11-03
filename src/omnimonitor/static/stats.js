/* OmniMonitor stats page. No external requests; data from ./api/stats only. */
(function () {
  "use strict";
  var data = null, mode = "daily";
  var $ = function (id) { return document.getElementById(id); };

  function fmtTokens(n) {
    if (n === null || n === undefined) return "–";
    var a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "B";
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
    if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "K";
    return String(n);
  }
  function fmtDur(s) {
    if (s === null || s === undefined) return "–";
    s = Math.round(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + x + "s";
    return x + "s";
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
    return d.toLocaleString(undefined, iso.length === 10 ? { month: "short", day: "numeric", year: "numeric" }
      : { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
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
        "DEMO MODE - every number on this page is fake sample data. Run `omnimonitor setup` to use your Grok account."));
    }
    if (st.error) b.appendChild(el("div", "banner err", "Last refresh failed: " + st.error +
      (u ? " (showing the last good value)" : "")));
    $("sub").textContent = "Updated " + (st.last_ok ? fmtDate(st.last_ok) : "never") +
      " · source: " + (st.source || "?") + " · tokens: " + (d.provider || "?") +
      (d.data_since ? " · token data since " + fmtDate(d.data_since) : "");

    if (u) {
      var rem = Math.round(u.remaining_percent);
      $("remaining").textContent = rem + "%";
      $("barfill").style.width = rem + "%";
      $("barfill").style.background = color(rem);
      $("reset").textContent = u.resets_at ? "Resets " + fmtDate(u.resets_at) : "Reset time not reported";
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
  }

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
