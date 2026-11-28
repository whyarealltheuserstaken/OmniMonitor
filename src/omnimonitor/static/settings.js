/* OmniMonitor settings page. Reads ./api/settings, writes POST ./api/settings (CSRF header). */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); }, el = OM.el;
  var csrf = "", initial = null;
  if (window.OMApp) OMApp.shell("settings");
  var BOOLS = ["notifications", "notify_on_reset", "notify_on_pace", "estimate_weekly_limit", "status_badges", "icon_text", "mini_on_top", "update_check"];
  var TEXTS = ["display_name", "theme", "accent_color", "icon_style", "icon_value", "tray_suite", "update_channel"];
  var NUMS = ["heatmap_weeks", "poll_interval_seconds", "estimate_min_used_percent",
              "provider_poll_seconds", "budget_alert_percent"];
  var CARD_LABELS = { "grok-suite": "Grok suite breakdown", forecast: "Pace & forecast", burn: "Burn rate", limit: "Tokens this week / limit",
    heatmap: "Heatmap", breakdown: "Breakdown", tools: "· by tool", models: "Models", projects: "· by project",
    cost: "Cost", insights: "Insights", providers: "All AI tools / providers", budgets: "Budgets" };
  var KIND = { local_logs: "local logs", api: "account API", web_session: "web session (opt-in)" };
  var BROWSERS = [["auto","Auto (first that works)"],["chrome","Chrome"],["edge","Edge"],
    ["brave","Brave"],["arc","Arc"],["firefox","Firefox"],["safari","Safari (macOS, needs FDA)"]];

  function providersTable(ro, s) {
    var t = $("providers_table"); t.textContent = "";
    var on = {}, keys = ro.keys || {}, sess = ro.sessions || {};
    Object.keys(s.providers || {}).forEach(function (k) { on[k] = s.providers[k] || {}; });
    (s.extra_token_sources || []).forEach(function (k) { if (!(k in on)) on[k] = {enabled: true}; });
    (ro.catalog || []).forEach(function (p) {
      var tr = el("tr"), a = el("td"), l = el("label"), c = el("input");
      var cur = on[p.id] || {};
      c.type = "checkbox"; c.value = p.id; c.checked = !!cur.enabled; c.setAttribute("data-pv", p.id);
      l.appendChild(c); l.appendChild(document.createTextNode(" " + p.name)); a.appendChild(l);
      var b = el("td", "s", (KIND[p.kind] || p.kind) + (p.status === "partial" ? " · partial" : p.status === "experimental" ? " · experimental" : ""));
      var k = el("td", "s");
      if (p.kind === "api") {
        k.textContent = (p.env || []).filter(function (n) { return /KEY|TOKEN/.test(n); }).map(function (n) {
          return n + ": " + (keys[n] === "env" ? "set (env)" : keys[n] === "vault" ? "set (encrypted vault)" : keys[n] === "file" ? "set (secrets file)" : "not set");
        }).join(" · ");
      } else if (p.kind === "web_session") {
        k.appendChild(document.createTextNode(
          "Off by default. Uses only this provider's own usage URL with a session you approve. Cookies stay on this computer, are never shown or exported. "));
        k.appendChild(document.createTextNode(p.shows || ""));
        var row = el("div", "checks"); row.style.marginTop = "6px";
        var auth = el("select"); auth.setAttribute("data-pv-auth", p.id);
        var authOpts = [["off","Auth: off"],["login","In-app sign-in window"],["browser","Import from browser"]];
        if (p.id === "cursor") authOpts.push(["cursor_app", "Use signed-in Cursor app"]);
        authOpts.forEach(function (o) {
          var op = el("option", "", o[1]); op.value = o[0]; if ((cur.auth || "off") === o[0]) op.selected = true; auth.appendChild(op);
        });
        var br = el("select"); br.setAttribute("data-pv-browser", p.id);
        BROWSERS.forEach(function (o) {
          var op = el("option", "", o[1]); op.value = o[0]; if ((cur.browser || "auto") === o[0]) op.selected = true; br.appendChild(op);
        });
        row.appendChild(auth); row.appendChild(document.createTextNode(" ")); row.appendChild(br);
        var st = el("span", "s"); st.style.marginLeft = "8px";
        st.textContent = sess[p.id] ? ("session: stored in " + sess[p.id]) : "session: none";
        row.appendChild(st);
        var btns = el("div", "checks"); btns.style.marginTop = "4px";
        function btn(label, act) {
          var b = el("button", "", label); b.type = "button"; b.setAttribute("data-session", act); b.setAttribute("data-pid", p.id);
          btns.appendChild(b); btns.appendChild(document.createTextNode(" "));
        }
        btn("Sign in…", "login"); btn("Import now", "import"); btn("Sign out / forget", "forget");
        k.appendChild(row); k.appendChild(btns);
        if (p.id === "cursor") {
          var appNote = el("div", "s");
          appNote.textContent = "“Use signed-in Cursor app” is off unless you select it. It reads cursorAuth/accessToken from the Cursor app's state.vscdb on this computer and does not copy the token into OmniMonitor. On macOS an encrypted token asks once for the Cursor Safe Storage keychain item.";
          k.appendChild(appNote);
        }
        if (p.status === "experimental") {
          var warn = el("div", "s"); warn.textContent = "Experimental: not yet verified against a live signed-in response in OmniMonitor.";
          k.appendChild(warn);
        }
      } else k.textContent = p.shows;
      if (p.status === "experimental" && p.kind !== "web_session") k.textContent += " — experimental: undocumented format, numbers may be wrong.";
      if (p.note) k.textContent += " " + p.note;
      tr.appendChild(a); tr.appendChild(b); tr.appendChild(k); t.appendChild(tr);
    });
    var sel = $("tray_suite"); sel.textContent = "";
    (ro.tray_choices || []).forEach(function (o) {
      var op = el("option", "", o.name); op.value = o.id; sel.appendChild(op);
    });
  }

  /* ---- layout editor: config "display", "suite_colors", "source_poll_seconds" ---- */
  var SURFACE_LABELS = { tray: "Tray icon", menu: "Tray menu", dashboard: "Dashboard",
    mini: "Mini view", statusline: "Status line" };
  var FIELD_LABELS = { limit: "limit %", reset: "reset time", tokens: "tokens", cost: "cost",
    money: "balance / spend" };

  function box(checked, attr, value, label) {
    var l = el("label"), c = el("input");
    c.type = "checkbox"; c.checked = checked; c.setAttribute(attr, value);
    l.appendChild(c); l.appendChild(document.createTextNode(" " + label));
    return l;
  }
  function isHidden(s, surface, ids) {
    var h = (((s.display || {}).hide) || {})[surface] || [];
    return ids.some(function (i) { return h.indexOf(i) >= 0; });
  }
  function groupRows(tr) {  // a suite row plus its source rows
    var rows = [tr], n = tr.nextSibling;
    while (n && !n.hasAttribute("data-suite")) { rows.push(n); n = n.nextSibling; }
    return rows;
  }
  function move(tr, dir) {
    var t = tr.parentNode;
    if (tr.hasAttribute("data-suite")) {
      var mine = groupRows(tr), other;
      if (dir < 0) {
        other = tr.previousSibling;
        while (other && !other.hasAttribute("data-suite")) other = other.previousSibling;
        if (other) mine.forEach(function (r) { t.insertBefore(r, other); });
      } else {
        other = mine[mine.length - 1].nextSibling;
        if (other) groupRows(other).forEach(function (r) { t.insertBefore(r, tr); });
      }
      return;
    }
    var sib = dir < 0 ? tr.previousSibling : tr.nextSibling;
    if (!sib || sib.hasAttribute("data-suite")) return;  // a source stays inside its suite
    t.insertBefore(dir < 0 ? tr : sib, dir < 0 ? sib : tr);
  }
  function layoutRow(s, ro, item, isSuite) {
    var tr = el("tr"), key = isSuite ? "suite:" + item.id : item.id;
    tr.setAttribute("data-key", key);
    if (isSuite) tr.setAttribute("data-suite", item.id);
    var name = el("td");
    [[-1, "↑", "up"], [1, "↓", "down"]].forEach(function (d) {
      var b = el("button", "", d[1]); b.type = "button"; b.title = "Move " + item.name + " " + d[2];
      b.setAttribute("aria-label", b.title);
      b.addEventListener("click", function () { move(tr, d[0]); });
      name.appendChild(b); name.appendChild(document.createTextNode(" "));
    });
    name.appendChild(el(isSuite ? "strong" : "span", isSuite ? "" : "s", (isSuite ? "" : "↳ ") + item.name));
    tr.appendChild(name);
    var where = el("td", "s"), wrap = el("div", "checks");
    (ro.surfaces || []).forEach(function (sf) {
      // "deepseek" alone means the DeepSeek source, never the whole suite
      var ids = isSuite ? (item.shared_id ? [key] : [key, item.id]) : [item.id];
      wrap.appendChild(box(!isHidden(s, sf, ids), "data-show", sf, SURFACE_LABELS[sf] || sf));
    });
    where.appendChild(wrap);
    if (isSuite) {
      var cur = (s.suite_colors || {})[item.id];
      var own = box(!!cur, "data-usecolor", item.id, "own colour ");
      var col = el("input"); col.type = "color"; col.value = cur || s.accent_color || "#2a8bd8";
      col.setAttribute("data-color", item.id); col.setAttribute("aria-label", item.name + " colour");
      col.addEventListener("input", function () { own.querySelector("input").checked = true; });
      where.appendChild(own); where.appendChild(col);
    } else if (item.account) {
      var l = el("label", "", "refresh every (s) "), n = el("input");
      n.type = "number"; n.min = "300"; n.max = "86400"; n.style.width = "100px";
      n.value = (s.source_poll_seconds || {})[item.id] || ""; n.setAttribute("data-poll", item.id);
      l.appendChild(n); where.appendChild(l);
    }
    tr.appendChild(where);
    return tr;
  }
  function layoutEditor(ro, s) {
    var t = $("layout"); t.textContent = "";
    (ro.suites || []).forEach(function (su) {
      t.appendChild(layoutRow(s, ro, su, true));
      (su.sources || []).forEach(function (src) { t.appendChild(layoutRow(s, ro, src, false)); });
    });
    if (!(ro.suites || []).length) {
      var empty = el("tr"); empty.appendChild(el("td", "s", "Nothing is switched on yet - use Setup."));
      t.appendChild(empty);
    }
    var f = $("layout_fields"); f.textContent = "";
    (ro.surfaces || []).forEach(function (sf) {
      if (sf === "tray") return;
      var tr = el("tr"); tr.appendChild(el("td", "", SURFACE_LABELS[sf] || sf));
      var td = el("td", "s"), wrap = el("div", "checks");
      wrap.setAttribute("data-fields", sf);
      (ro.all_fields || []).forEach(function (k) {
        wrap.appendChild(box(((ro.fields || {})[sf] || []).indexOf(k) >= 0, "data-field", k, FIELD_LABELS[k] || k));
      });
      td.appendChild(wrap); tr.appendChild(td); f.appendChild(tr);
    });
  }
  function collectLayout(out) {
    var s = initial || {}, ro = s._readonly || {};
    var rows = Array.prototype.slice.call(document.querySelectorAll("#layout tr[data-key]"));
    var shown = {};
    rows.forEach(function (r) { shown[r.getAttribute("data-key")] = true; });
    // keep entries for things that aren't listed here (switched off, or set from the CLI)
    var old = s.display || {}, shared = {};
    (ro.suites || []).forEach(function (su) { if (su.shared_id) shared[su.id] = true; });
    function unlisted(id) { return !shown[id] && !(shown["suite:" + id] && !shared[id]); }
    var order = rows.map(function (r) { return r.getAttribute("data-key"); })
      .concat((old.order || []).filter(unlisted));
    var hide = {};
    (ro.surfaces || []).forEach(function (sf) {
      var ids = (((old.hide || {})[sf]) || []).filter(unlisted);
      rows.forEach(function (r) {
        var c = r.querySelector('input[data-show="' + sf + '"]');
        if (c && !c.checked) ids.push(r.getAttribute("data-key"));
      });
      if (ids.length) hide[sf] = ids;
    });
    var fields = Object.assign({}, old.fields || {});
    Array.prototype.forEach.call(document.querySelectorAll("#layout_fields [data-fields]"), function (w) {
      fields[w.getAttribute("data-fields")] = Array.prototype.filter.call(w.querySelectorAll("input"),
        function (c) { return c.checked; }).map(function (c) { return c.getAttribute("data-field"); });
    });
    out.display = { order: order, hide: hide, fields: fields };
    var colors = Object.assign({}, s.suite_colors || {});
    Array.prototype.forEach.call(document.querySelectorAll("#layout input[data-usecolor]"), function (c) {
      var id = c.getAttribute("data-usecolor");
      if (c.checked) colors[id] = document.querySelector('#layout input[data-color="' + id + '"]').value;
      else delete colors[id];
    });
    out.suite_colors = colors;
    var poll = Object.assign({}, s.source_poll_seconds || {});
    Array.prototype.forEach.call(document.querySelectorAll("#layout input[data-poll]"), function (n) {
      if (n.value) poll[n.getAttribute("data-poll")] = Number(n.value);
      else delete poll[n.getAttribute("data-poll")];
    });
    out.source_poll_seconds = poll;
  }

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
    var ro = s._readonly || {};
    providersTable(ro, s);
    layoutEditor(ro, s);
    if (s.tray_suite) $("tray_suite").value = s.tray_suite;
    var bu = s.budgets || {};
    ["daily", "weekly", "monthly"].forEach(function (k) { $("budget_" + k).value = bu[k] || 0; });
    $("provider_thresholds").value = s.provider_thresholds && Object.keys(s.provider_thresholds).length ? JSON.stringify(s.provider_thresholds) : "";
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
    var pv = {};
    Array.prototype.forEach.call(document.querySelectorAll("#providers_table input[data-pv]"), function (c) {
      var o = { enabled: c.checked };
      var auth = document.querySelector('select[data-pv-auth="' + c.value + '"]');
      var br = document.querySelector('select[data-pv-browser="' + c.value + '"]');
      if (auth) o.auth = auth.value;
      if (br) o.browser = br.value;
      pv[c.value] = o;
    });
    out.providers = pv;
    out.budgets = { daily: Number($("budget_daily").value) || 0, weekly: Number($("budget_weekly").value) || 0,
                    monthly: Number($("budget_monthly").value) || 0 };
    out.provider_thresholds = $("provider_thresholds").value.trim() || "{}";
    collectLayout(out);
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
  document.addEventListener("click", function (ev) {
    var b = ev.target && ev.target.closest && ev.target.closest("[data-session]");
    if (!b || !window.OM || !OM.appPost) return;
    ev.preventDefault();
    var act = b.getAttribute("data-session"), pid = b.getAttribute("data-pid");
    var body = { provider: pid };
    if (act === "import") {
      var br = document.querySelector('select[data-pv-browser="' + pid + '"]');
      body.browser = br ? br.value : "auto";
      OM.appPost("session-import", body).then(function (r) {
        OM.toast(r.ok ? ("Imported (" + (r.stored || "ok") + ")") : (r.error || "import failed"));
        if (r.ok) load();
      });
    } else if (act === "login") {
      OM.appPost("session-login", body).then(function (r) {
        OM.toast(r.ok ? (r.message || "Opening sign-in…") : (r.error || "login failed"));
      });
    } else if (act === "forget") {
      OM.appPost("session-forget", body).then(function (r) {
        OM.toast(r.ok ? "Signed out" : (r.error || "forget failed"));
        if (r.ok) load();
      });
    }
  });
  load();
})();
