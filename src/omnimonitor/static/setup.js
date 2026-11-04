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

  function renderLogin(s) {
    var box = document.getElementById("grok-login");
    if (!box) {
      box = el("div", "dep"); box.id = "grok-login";
      var deps = document.getElementById("deps"); deps.parentNode.insertBefore(box, deps);
    }
    box.textContent = "";
    var g = s.grok_login || {}, left = el("div", "");
    var ok = g.state === "ok";
    var t = el("h3", "", "Grok login ");
    t.appendChild(el("span", "pill " + (ok ? "ok" : "miss"), ok ? "logged in" :
      ({ not_installed: "CLI missing", not_logged_in: "not logged in", expired: "expired" }[g.state] || "?")));
    left.appendChild(t);
    left.appendChild(el("div", "meta", g.message || ""));
    if (!ok && g.state !== "not_installed") {
      var c = el("div", "meta", "Login is interactive: open Terminal and run ");
      c.appendChild(el("code", "", g.login_command || "grok login")); left.appendChild(c);
    }
    left.appendChild(el("div", "meta", s.demo ? "This app is showing DEMO (fake) numbers." :
      "Real Grok usage is on" + (s.last_ok ? " · updated " + new Date(s.last_ok).toLocaleString() : "") +
      (s.error ? " · last error: " + s.error : "")));
    box.appendChild(left);
    var right = el("div", ""), out = el("div", "meta", "");
    var re = el("button", "", "Re-check"); re.addEventListener("click", load); right.appendChild(re);
    if (s.demo) {
      var use = el("button", "primary", "Use my real Grok usage"); use.disabled = !ok;
      use.addEventListener("click", function () {
        use.disabled = true; out.textContent = "Checking your Grok usage…";
        post("api/setup/use-real", {}).then(function (r) {
          out.textContent = r.ok ? "Done: " + Math.round(r.remaining_percent) + "% remaining." :
            "Failed: " + (r.error || "?");
          setTimeout(load, 800);
        }).catch(function () { out.textContent = "Request failed."; use.disabled = false; });
      });
      right.appendChild(document.createTextNode(" ")); right.appendChild(use);
    }
    right.appendChild(out); box.appendChild(right);
  }

  function load() {
    fetch("api/setup", { credentials: "same-origin" }).then(function (r) { return r.json(); })
      .then(function (s) {
        csrf = s._csrf || "";
        document.getElementById("root-banner").hidden = !s.root;
        renderLogin(s);
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
