/* OmniMonitor mini view: the essentials in a small window. Data from ./api/stats only. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); }, el = OM.el, T = OM.fmtTokens;
  function color(p) { return p >= 50 ? "var(--ok)" : p >= 20 ? "var(--warn)" : "var(--bad)"; }
  function row(k, v, reason) {
    var r = el("div", "mrow"); r.appendChild(el("span", "k", k));
    var s = el("span", "v"); if (v === null || v === undefined) s.appendChild(OM.na(reason)); else s.textContent = v;
    r.appendChild(s); return r;
  }
  function render(d) {
    var st = d.status || {}, u = st.usage, p = d.personal || {};
    OM.applyTheme(p);
    $("who").textContent = (p.display_name || "OmniMonitor") + (d.is_demo ? " · DEMO" : "");
    var b = $("banners"); b.textContent = "";
    if (d.is_demo || (u && u.is_demo)) b.appendChild(el("div", "banner demo", "DEMO - fake sample data"));
    if (st.error) b.appendChild(el("div", "banner err", "Last refresh failed: " + st.error));
    if (u) {
      var rem = Math.round(u.remaining_percent);
      $("remaining").textContent = rem + "%";
      $("barfill").style.width = rem + "%"; $("barfill").style.background = color(rem);
      $("reset").textContent = u.resets_at ? "Resets " + (OM.fmtCountdown(u.resets_at) ? OM.fmtCountdown(u.resets_at) + " · " : "") + OM.fmtDate(u.resets_at) : "Reset time not reported";
    } else { $("remaining").textContent = "–"; $("reset").textContent = st.error || "No data yet"; }
    var rows = $("rows"); rows.textContent = "";
    var a = (d.forecast || {}).average || {}, why = (d.lifetime_tokens || {}).reason;
    rows.appendChild(row("≈ Used at reset", a.percent_at_reset !== undefined ? "≈" + Math.round(Math.min(a.percent_at_reset, 999)) + "%" : null, a.reason));
    if (a.runs_out_at) rows.appendChild(row("≈ Runs out", OM.fmtDate(a.runs_out_at)));
    var wk = d.week_tokens || {};
    rows.appendChild(row("Tokens this week", wk.value !== null && wk.value !== undefined ? T(wk.value) : null, wk.reason));
    var today = (d.breakdowns || {}).today;
    rows.appendChild(row("Tokens today", today ? T(today.tokens) : null, why || "nothing today"));
    if (today) today.tools.forEach(function (t) { rows.appendChild(row("  " + t.name, T(t.tokens))); });
    var bu = d.burn;
    rows.appendChild(row("Last hour", bu ? T(bu.last_hour) : null, why));
    var c = ((d.cost || {}).ranges || {}).today;
    if (c && c.estimated_usd !== null) rows.appendChild(row("≈ Cost today (estimate)", OM.fmtUsd(c.estimated_usd)));
    $("upd").textContent = "Updated " + (st.last_ok ? OM.fmtDate(st.last_ok) : "never");
  }
  function load() {
    OM.getJSON("api/stats").then(function (r) { render(r.body); })
      .catch(function (e) { $("upd").textContent = "Could not load: " + e; });
  }
  load(); setInterval(load, 30000);
})();
