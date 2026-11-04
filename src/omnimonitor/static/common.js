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
  window.OM = { fmtTokens: fmtTokens, fmtUsd: fmtUsd, fmtDate: fmtDate, el: el, na: na,
                applyTheme: applyTheme, getJSON: getJSON };
})();
