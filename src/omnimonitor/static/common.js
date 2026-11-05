/* OmniMonitor shared helpers (no external requests). Exposes window.OM. */
(function () {
  "use strict";
  function fmtTokens(n) {
    if (n === null || n === undefined) return "–";
    var a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "B";
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
    if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "K";
    return String(Math.round(n));
  }
  function fmtUsd(v) {
    if (v === null || v === undefined) return null;
    return "$" + (v >= 100 ? v.toFixed(0) : v.toFixed(2));
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
    return d.toLocaleString(undefined, iso.length === 10 ? { month: "short", day: "numeric", year: "numeric" }
      : { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  }
  // "in 2d 4h" style countdown to a unix-seconds or ISO time; "" when unknown or past.
  function fmtCountdown(t) {
    if (!t) return "";
    var ms = (typeof t === "number" ? t * 1000 : new Date(t).getTime()) - Date.now();
    if (!(ms > 0)) return "";
    var m = Math.floor(ms / 60000), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60);
    return "in " + (d ? d + "d " + h + "h" : h ? h + "h " + (m % 60) + "m" : (m % 60) + "m");
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function na(reason, text) {
    var s = el("span", "na", text || "not available");
    s.title = reason || "not available";
    return s;
  }
  function hexOk(c) { return typeof c === "string" && /^#[0-9a-fA-F]{6}$/.test(c); }
  function mix(hex, t) { // blend accent toward black (t<0) or white (t>0)
    var n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    var tgt = t < 0 ? 0 : 255, k = Math.abs(t);
    function c(x) { return Math.round(x + (tgt - x) * k); }
    return "rgb(" + c(r) + "," + c(g) + "," + c(b) + ")";
  }
  function applyTheme(p) {
    p = p || {};
    var theme = p.theme || "dark";
    if (theme === "auto") theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    document.body.classList.toggle("light", theme === "light");
    var acc = hexOk(p.accent_color) ? p.accent_color : "#2a8bd8", s = document.body.style;
    s.setProperty("--accent", acc);
    s.setProperty("--g1", mix(acc, theme === "light" ? 0.7 : -0.6));
    s.setProperty("--g2", mix(acc, theme === "light" ? 0.4 : -0.3));
    s.setProperty("--g3", acc);
    s.setProperty("--g4", mix(acc, theme === "light" ? -0.35 : 0.4));
  }
  function getJSON(url) {
    return fetch(url, { cache: "no-store", credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
    });
  }
  /* ---- native app mode (pywebview: WKWebView / WebView2) --------------------------------
     Embedded web views ignore <a download>, send target=_blank / window.open to the system
     browser (or drop them) and often block the clipboard. In the app window (opened with
     ?app=1) we route those clicks to CSRF-protected POST api/app/<action> calls and the tray
     process does the native work. In a normal browser, links keep their usual behavior. */
  try {
    if (/[?&]app=1(&|$)/.test(location.search)) sessionStorage.setItem("om_app", "1");
  } catch (e) { /* storage disabled: fall back to the URL flag only */ }
  function isApp() {
    try { return sessionStorage.getItem("om_app") === "1" || /[?&]app=1(&|$)/.test(location.search); }
    catch (e) { return /[?&]app=1(&|$)/.test(location.search); }
  }
  var appInfo = null;
  function appMeta() {
    if (appInfo) return Promise.resolve(appInfo);
    return getJSON("api/app").then(function (r) {
      if (!r.ok) throw new Error("app actions unavailable (HTTP " + r.status + ")");
      appInfo = r.body; return appInfo;
    });
  }
  function appPost(action, body) {
    return appMeta().then(function (m) {
      if ((m.actions || []).indexOf(action) < 0) throw new Error("'" + action + "' is only available in the OmniMonitor app (tray)");
      return fetch("api/app/" + action, { method: "POST", cache: "no-store", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-OmniMonitor-CSRF": m._csrf },
        body: JSON.stringify(body || {}) }).then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok || j.ok === false) throw new Error(j.error || (j.errors && j.errors._) || ("HTTP " + r.status));
          return j;
        });
      });
    });
  }
  function toast(msg, bad) {
    var t = document.getElementById("om-toast");
    if (!t) {
      t = el("div"); t.id = "om-toast"; t.setAttribute("role", "status");
      t.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:99;max-width:90vw;" +
        "padding:9px 14px;border-radius:10px;font-size:13px;box-shadow:0 6px 24px rgba(0,0,0,.35);background:var(--card,#222);" +
        "color:var(--fg,#eee);border:1px solid var(--line,#444);word-break:break-all";
      document.body.appendChild(t);
    }
    t.textContent = msg; t.style.borderColor = bad ? "#e5534b" : "var(--line,#444)"; t.style.display = "block";
    clearTimeout(t._h); t._h = setTimeout(function () { t.style.display = "none"; }, bad ? 9000 : 6000);
  }
  function exportArgs(href) {
    var m = /api\/export\.(csv|json)(?:\?kind=([a-z\-]+))?/.exec(href || "");
    return m ? { fmt: m[1], kind: m[2] || "daily" } : null;
  }
  function openView(view, page) {
    if (!isApp()) { location.href = view === "mini" ? "mini" : "./" + (page || ""); return; }
    appPost("window", { view: view, page: page || "" }).catch(function (e) {
      toast("Could not switch window: " + e.message, true);
    });
  }
  function copyText(text) {
    function fallback() {
      var ta = el("textarea"); ta.value = text; ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;left:-9999px;top:0"; document.body.appendChild(ta); ta.select();
      var ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta); return ok;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return fallback(); });
    }
    return Promise.resolve(fallback());
  }
  function reportProblem() {
    var old = document.getElementById("om-diag"); if (old) old.parentNode.removeChild(old);
    var wrap = el("div"); wrap.id = "om-diag";
    wrap.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:98;display:flex;align-items:center;justify-content:center";
    var box = el("div", "card"); box.style.cssText = "width:min(680px,92vw);max-height:86vh;display:flex;flex-direction:column;gap:8px";
    box.appendChild(el("b", null, "Report a problem"));
    box.appendChild(el("div", "sub", "Diagnostics below contain no API keys, tokens or cookies (and your home folder is shown as ~). " +
      "Copy them into your bug report or save them as a file."));
    var ta = el("textarea"); ta.readOnly = true; ta.style.cssText = "width:100%;height:46vh;font:12px ui-monospace,Menlo,monospace";
    ta.value = "Collecting…"; box.appendChild(ta);
    var row = el("div"); row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap";
    var bCopy = el("button", null, "Copy diagnostics"), bSave = el("button", null, "Save to Downloads"), bClose = el("button", null, "Close");
    row.appendChild(bCopy); row.appendChild(bSave); row.appendChild(bClose); box.appendChild(row);
    wrap.appendChild(box); document.body.appendChild(wrap);
    bClose.onclick = function () { wrap.parentNode.removeChild(wrap); };
    wrap.addEventListener("click", function (ev) { if (ev.target === wrap) bClose.onclick(); });
    appPost("diagnostics", {}).then(function (j) { ta.value = j.text; }, function (e) { ta.value = "Could not collect diagnostics: " + e.message; });
    bCopy.onclick = function () {
      copyText(ta.value).then(function (ok) {
        if (ok) { toast("Diagnostics copied to the clipboard"); return; }
        ta.focus(); ta.select(); toast("Clipboard blocked here: the text is selected, press Cmd/Ctrl+C, or use Save to Downloads", true);
      });
    };
    bSave.onclick = function () {
      appPost("diagnostics", { save: true }).then(function (j) { toast("Saved " + j.path); },
        function (e) { toast("Save failed: " + e.message, true); });
    };
  }
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest ? ev.target.closest("a,button") : null; if (!a) return;
    var act = a.getAttribute("data-action");
    if (act === "report") { ev.preventDefault(); reportProblem(); return; }
    if (act === "browser") { ev.preventDefault(); appPost("browser", { page: a.getAttribute("data-page") || "" })
      .catch(function (e) { toast("Could not open the browser: " + e.message, true); }); return; }
    var view = a.getAttribute("data-view");
    if (view && isApp()) { ev.preventDefault(); openView(view, a.getAttribute("data-page") || ""); return; }
    if (!isApp() || a.tagName !== "A") return;
    var href = a.getAttribute("href") || "", ex = exportArgs(href);
    if (ex && (a.hasAttribute("download") || /^api\/export/.test(href))) {
      ev.preventDefault();
      toast("Exporting…");
      appPost("export", ex).then(function (j) { toast("Saved " + j.path + " (shown in your file manager)"); },
        function (e) { toast("Export failed: " + e.message, true); });
      return;
    }
    if (/^https:\/\//i.test(href) || a.target === "_blank") {
      ev.preventDefault();
      if (!/^https:\/\//i.test(a.href)) { toast("Only https links open outside the app", true); return; }
      appPost("open-url", { url: a.href }).then(function () { toast("Opened in your browser"); },
        function (e) { toast("Could not open link: " + e.message, true); });
    }
  }, true);
  if (isApp()) {
    window.open = function (u) {  // nothing in the app should spawn web-view popups
      if (u && /^https:\/\//i.test(String(u))) appPost("open-url", { url: String(u) });
      return null;
    };
  }

  window.OM = { fmtTokens: fmtTokens, fmtUsd: fmtUsd, fmtDate: fmtDate, fmtCountdown: fmtCountdown, el: el, na: na,
                applyTheme: applyTheme, getJSON: getJSON, isApp: isApp, appPost: appPost, toast: toast,
                openView: openView, copyText: copyText, reportProblem: reportProblem };
})();
