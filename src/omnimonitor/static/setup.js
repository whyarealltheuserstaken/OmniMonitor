/* OmniMonitor Setup page. GET ./api/setup; POST ./api/setup/prepare then ./api/setup/run
   (CSRF header). Two clicks per dependency: "Review installer" downloads it and shows the
   command + SHA256; "Run installer" only runs that exact file, after a confirm checkbox. */
(function () {
  "use strict";
  if (window.OMApp) OMApp.shell("setup");
  var el = OM.el, csrf = "";

  function post(path, body) {
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-OmniMonitor-CSRF": csrf },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  function renderPlan(box, d, plan) {
    box.textContent = "";
    if (!plan.ok) { box.appendChild(el("div", "", "Could not prepare: " + plan.error)); return; }
    box.appendChild(el("div", "", "Source: " + plan.url));
    var c = el("div", ""); c.appendChild(el("span", "", "Command: "));
    c.appendChild(el("code", "", plan.command_str)); box.appendChild(c);
    var h = el("div", ""); h.appendChild(el("span", "", "SHA256: "));
    h.appendChild(el("code", "", plan.sha256)); box.appendChild(h);
    box.appendChild(el("div", "", "First lines of the script:"));
    box.appendChild(el("pre", "", String(plan.preview || "")));
    var lab = el("label", ""), cb = document.createElement("input");
    cb.type = "checkbox"; lab.appendChild(cb);
    lab.appendChild(document.createTextNode(" I reviewed this and want to run it as my user"));
    box.appendChild(lab);
    var run = el("button", "primary", "Run installer"); run.disabled = true;
    var dry = el("button", "", "Dry run");
    cb.addEventListener("change", function () { run.disabled = !cb.checked; });
    var out = el("pre", "out", "");
    function go(dryRun) {
      run.disabled = dry.disabled = true; out.textContent = dryRun ? "Checking…" : "Running…";
      post("api/setup/run", { token: plan.token, sha256: plan.sha256, dry_run: dryRun })
        .then(function (r) {
          out.textContent = (r.ok ? "OK" : "Failed: " + (r.error || "exit " + r.rc)) +
            (r.output ? "\n" + r.output : "");
          if (!dryRun) setTimeout(load, 800); else { dry.disabled = false; run.disabled = !cb.checked; }
        }).catch(function () { out.textContent = "Request failed."; });
    }
    run.addEventListener("click", function () { go(false); });
    dry.addEventListener("click", function () { go(true); });
    var row = el("div", ""); row.appendChild(run); row.appendChild(document.createTextNode(" "));
    row.appendChild(dry); box.appendChild(row); box.appendChild(out);
  }

  function renderDep(d) {
    var row = el("div", "dep"), left = el("div", "");
    var t = el("h3", "", d.name + " ");
    t.appendChild(el("span", "pill " + (d.installed ? "ok" : "miss"),
      d.installed ? "installed" : "missing"));
    left.appendChild(t);
    left.appendChild(el("div", "meta", "Needed by: " + d.providers.join(", ") +
      (d.path ? " · " + d.path : "")));
    if (d.notes) left.appendChild(el("div", "meta", d.notes));
    if (!d.installed && d.manual) left.appendChild(el("div", "meta", "Manual install: " + d.manual));
    if (d.docs) { var a = el("a", "meta", "Official instructions"); a.href = d.docs;
      a.target = "_blank"; a.rel = "noopener noreferrer"; left.appendChild(a); }
    row.appendChild(left);
    var right = el("div", "");
    if (!d.installed && d.installer) {
      var b = el("button", "", "Review installer"), plan = el("div", "plan");
      plan.hidden = true;
      b.addEventListener("click", function () {
        b.disabled = true; plan.hidden = false; plan.textContent = "Downloading from " + d.installer + "…";
        post("api/setup/prepare", { dep: d.id }).then(function (p) { renderPlan(plan, d, p); b.disabled = false; })
          .catch(function () { plan.textContent = "Request failed."; b.disabled = false; });
      });
      right.appendChild(b); row.appendChild(right); row.appendChild(plan);
    } else row.appendChild(right);
    return row;
  }

  /* ---- accounts and tools (api/setup "onboarding"; actions POST api/app/onboard) ---- */
  var PILL = { connected: "ok", found: "miss", sign_in: "miss", add_key: "miss", attention: "bad",
    not_available: "" };
  var LABEL = { connected: "Connected", found: "Found", sign_in: "Sign in", add_key: "Add key",
    attention: "Needs attention", not_available: "Not available" };
  var open = {};  // which suites have their source list expanded

  function pill(state) { return el("span", "pill " + PILL[state], LABEL[state] || state); }

  function act(body, button) {
    if (button) button.disabled = true;
    OM.appPost("onboard", body).then(function (r) {
      OM.toast(r.message || "Saved.");
      // the next read happens in the background: look again shortly
      loadSuites(); setTimeout(loadSuites, 2500); setTimeout(loadSuites, 8000);
    }, function (e) { OM.toast(e.message, true); if (button) button.disabled = false; });
  }

  function actionButton(a) {
    var b = el("button", "primary", a.type === "add_key" ? "Copy key command" : a.label);
    b.type = "button";
    b.addEventListener("click", function () {
      if (a.type === "enable") act({ action: "enable", sources: a.sources }, b);
      else if (a.type === "signin") act({ action: "signin", source: a.source }, b);
      else OM.copyText(a.command).then(function (ok) {
        OM.toast((ok ? "Copied. Run it in a terminal: " : "Run in a terminal: ") + a.command);
      });
    });
    return b;
  }

  function sourceRow(r) {
    var row = el("div", "dep"), left = el("div", ""), right = el("div", "");
    var t = el("div", "", r.name + " "); t.appendChild(pill(r.state)); left.appendChild(t);
    var bits = [r.detail];
    if (r.verified === "fixtures") bits.push("not live-verified");
    else if (r.verified === "live") bits.push("checked on real data");
    if (r.status === "experimental") bits.push("experimental");
    left.appendChild(el("div", "meta", bits.filter(Boolean).join(" · ")));
    function button(label, body) {
      var b = el("button", "", label); b.type = "button";
      b.addEventListener("click", function () { act(body, b); });
      right.appendChild(b); right.appendChild(document.createTextNode(" "));
    }
    if (r.state === "found") button("Turn on", { action: "enable", sources: [r.id] });
    if (r.state === "sign_in") button("Sign in…", { action: "signin", source: r.id });
    if (r.state === "attention" && r.can_sign_in) button("Sign in again…", { action: "signin", source: r.id });
    if (r.state === "add_key") right.appendChild(actionButton({ type: "add_key", command: r.command }));
    if (r.cursor_app) button("Use signed-in Cursor app", { action: "cursor_app" });
    if (r.enabled && r.id !== "grok_sessions") button("Turn off", { action: "disable", sources: [r.id] });
    row.appendChild(left); row.appendChild(right);
    return row;
  }

  function renderSuites(o) {
    var box = document.getElementById("suites"); box.textContent = "";
    var suites = o.suites || [];
    document.getElementById("demo-banner").hidden = !o.demo;
    document.getElementById("connected-summary").textContent = "· " +
      suites.filter(function (s) { return s.state === "connected"; }).length + " of " + suites.length + " connected";
    suites.forEach(function (s) {
      var wrap = el("div", "dep"), left = el("div", ""), right = el("div", "");
      var t = el("h3", "", s.name + " "); t.appendChild(pill(s.state)); left.appendChild(t);
      var more = el("button", ""), list = el("div", "plan");
      more.type = "button";
      function show() {
        list.hidden = !open[s.id];
        more.textContent = open[s.id] ? "Hide sources" : "Sources (" + s.sources.length + ")";
        more.setAttribute("aria-expanded", String(!!open[s.id]));
      }
      more.addEventListener("click", function () { open[s.id] = !open[s.id]; show(); });
      left.appendChild(more);
      if (s.action) {
        right.appendChild(actionButton(s.action));
        if (s.action.type === "add_key") left.appendChild(el("div", "meta", s.action.command));
      }
      s.sources.forEach(function (r) { list.appendChild(sourceRow(r)); });
      show();
      wrap.appendChild(left); wrap.appendChild(right); wrap.appendChild(list); box.appendChild(wrap);
    });
  }

  function fetchSetup() {
    return fetch("api/setup", { cache: "no-store", credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (s) { csrf = s._csrf || ""; return s; });
  }

  function loadSuites() {
    return fetchSetup().then(function (s) { renderSuites(s.onboarding || {}); })
      .catch(function () { document.getElementById("suites").textContent = "Could not load setup status."; });
  }
  document.getElementById("leave-demo").addEventListener("click", function () {
    act({ action: "real" }, this);
  });
  setInterval(loadSuites, 15000);  // picks up finished sign-ins and first reads

  function load() {
    fetchSetup()
      .then(function (s) {
        document.getElementById("root-banner").hidden = !s.root;
        renderSuites(s.onboarding || {});
        var box = document.getElementById("deps"); box.textContent = "";
        var deps = s.deps || [], miss = deps.filter(function (d) { return !d.installed; }).length;
        document.getElementById("summary").textContent = deps.length ?
          "· " + (deps.length - miss) + "/" + deps.length + " installed" : "";
        if (!deps.length) box.appendChild(el("div", "sub", "No enabled provider needs a command-line tool."));
        deps.forEach(function (d) { box.appendChild(renderDep(d)); });
        var hist = document.getElementById("hist"); hist.textContent = "";
        var rows = s.history || [];
        if (!rows.length) { hist.appendChild(el("div", "sub", "Nothing installed from here yet.")); return; }
        var tb = el("table", "hist");
        rows.slice().reverse().forEach(function (h) {
          var tr = el("tr", "");
          [h.ts || "", h.dep || "", h.event || "", h.rc === undefined ? "" : (h.rc === 0 ? "ok" : "rc " + h.rc),
           (h.sha256 || "").slice(0, 12)].forEach(function (v) { tr.appendChild(el("td", "", String(v))); });
          tb.appendChild(tr);
        });
        hist.appendChild(tb);
      }).catch(function () { document.getElementById("deps").textContent = "Could not load setup status."; });
  }
  load();
})();
