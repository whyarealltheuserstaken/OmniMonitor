/* OmniMonitor settings page. Reads ./api/settings, writes POST ./api/settings (CSRF header). */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); }, el = OM.el;
  var csrf = "", initial = null;
  var BOOLS = ["notifications", "notify_on_reset", "notify_on_pace", "estimate_weekly_limit"];
  var TEXTS = ["display_name", "theme", "accent_color", "icon_style", "icon_value"];
  var NUMS = ["heatmap_weeks", "poll_interval_seconds", "estimate_min_used_percent"];
  var CARD_LABELS = { forecast: "Pace & forecast", burn: "Burn rate", limit: "Tokens this week / limit",
    heatmap: "Heatmap", breakdown: "Breakdown", tools: "· by tool", models: "Models", projects: "· by project",
    cost: "Cost", insights: "Insights" };
  var TOOLS = { claude_code: "Claude Code", codex: "Codex" };

  function checks(id, opts, chosen) {
    var box = $(id); box.textContent = "";
    Object.keys(opts).forEach(function (k) {
      var l = el("label"), c = el("input"); c.type = "checkbox"; c.value = k; c.checked = chosen.indexOf(k) >= 0;
      l.appendChild(c); l.appendChild(document.createTextNode(opts[k])); box.appendChild(l);
    });
  }
  function checked(id) {
    return Array.prototype.filter.call($(id).querySelectorAll("input"), function (c) { return c.checked; })
      .map(function (c) { return c.value; });
  }
  function fill(s) {
    csrf = s._csrf || ""; initial = s;
    OM.applyTheme(s);
    TEXTS.forEach(function (k) { if (s[k] !== undefined && s[k] !== null) $(k).value = s[k]; });
    NUMS.forEach(function (k) { if (s[k] !== undefined && s[k] !== null) $(k).value = s[k]; });
    BOOLS.forEach(function (k) { $(k).checked = !!s[k]; });
    $("notify_thresholds").value = (s.notify_thresholds || []).join(", ");
    $("prices").value = s.prices && Object.keys(s.prices).length ? JSON.stringify(s.prices, null, 2) : "";
    var cards = {}; ((s._readonly || {}).hideable_cards || []).forEach(function (c) { cards[c] = CARD_LABELS[c] || c; });
    checks("hidden_cards", cards, s.hidden_cards || []);
    checks("extra_token_sources", TOOLS, s.extra_token_sources || []);
    var ro = s._readonly || {};
    $("ro").textContent = "billing source: " + ro.source + " · token_source: " + ro.token_source +
      " · config: " + (ro.config_path || "?");
  }
  function collect() {
    var out = {};
    TEXTS.forEach(function (k) { out[k] = $(k).value; });
    NUMS.forEach(function (k) { out[k] = Number($(k).value); });
    BOOLS.forEach(function (k) { out[k] = $(k).checked; });
    out.notify_thresholds = $("notify_thresholds").value;
    out.prices = $("prices").value.trim() || "{}";
    out.hidden_cards = checked("hidden_cards");
    out.extra_token_sources = checked("extra_token_sources");
    return out;
  }
  function showErrors(errs) {
    Array.prototype.forEach.call(document.querySelectorAll(".err-t"), function (n) { n.remove(); });
    Object.keys(errs || {}).forEach(function (k) {
      var target = $(k), msg = el("div", "err-t", errs[k]);
      if (target && target.parentNode) target.parentNode.appendChild(msg);
      else $("msg").textContent = errs[k];
    });
  }
  $("form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    $("save").disabled = true; $("msg").textContent = "Saving…";
    fetch("api/settings", { method: "POST", cache: "no-store", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-OmniMonitor-CSRF": csrf },
      body: JSON.stringify(collect()) })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        $("save").disabled = false;
        if (res.ok) { showErrors({}); $("msg").textContent = res.changed && res.changed.length ? "Saved." : "Saved (no changes)."; load(); }
        else { $("msg").textContent = "Not saved - fix the fields marked in red."; showErrors(res.errors); }
      })
      .catch(function (e) { $("save").disabled = false; $("msg").textContent = "Could not save: " + e; });
  });
  function load() {
    OM.getJSON("api/settings").then(function (r) { fill(r.body); })
      .catch(function (e) { $("msg").textContent = "Could not load settings: " + e; });
    OM.getJSON("api/stats").then(function (r) {
      var b = $("banners"); b.textContent = "";
      if (r.body.is_demo) b.appendChild(el("div", "banner demo", "DEMO MODE - the stats are fake. Settings still save to your config file."));
      var t = (r.body.tools || []).map(function (x) { return x.name; });
      $("providers").textContent = t.length ? "Reporting now: " + t.join(", ") : "";
    });
  }
  load();
})();
