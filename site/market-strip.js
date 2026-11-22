/* NS FINANCIAL SERVICES - market strip, shown under the menu on every page.
   Each index's day's change on the last trading day, worked out as the median day's change in
   NAV of the regular-plan index funds that track it. Worked out every night from AMFI NAVs by
   the fund explorer (data/market.json). End of day, not live index values.
   Add to a page with:  <script src="https://funds.nsfinancialservices.co.in/market-strip.js" defer></script> */
(function () {
  "use strict";
  var me = document.currentScript;
  var BASE = me && me.src ? me.src.replace(/[^\/]*$/, "") : "https://funds.nsfinancialservices.co.in/";
  var EXPLORER = "https://funds.nsfinancialservices.co.in/";
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  var CSS =
    ".nsmk{background:#EEF2FC;border-bottom:1px solid #D8DEF0;font:500 .85rem/1.3 'Public Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#111633}" +
    ".nsmk *{box-sizing:border-box}" +
    ".nsmk-in{max-width:1120px;margin-inline:auto;padding:0 20px;display:flex;align-items:center;gap:14px;min-height:38px}" +
    ".nsmk-lab{flex:none;font:600 .72rem/1.2 'IBM Plex Mono',ui-monospace,Menlo,monospace;letter-spacing:.06em;text-transform:uppercase;color:#4A5274;text-decoration:none;white-space:nowrap}" +
    ".nsmk-lab:hover{color:#1B3DAB;text-decoration:underline}" +
    ".nsmk-vp{flex:1 1 auto;min-width:0;overflow:hidden}" +
    ".nsmk-row{display:flex;gap:22px;width:max-content;padding-block:9px}" +
    ".nsmk-it{white-space:nowrap}" +
    ".nsmk-it b{font-weight:600;margin-right:6px}" +
    ".nsmk-up{color:#0A7A3B;font-weight:600}.nsmk-dn{color:#B42318;font-weight:600}.nsmk-fl{color:#4A5274;font-weight:600}" +
    ".nsmk-run .nsmk-row{padding-right:22px;animation:nsmk-roll var(--nsmk-t,30s) linear infinite}" +
    ".nsmk-run:hover .nsmk-row,.nsmk-run:focus-within .nsmk-row{animation-play-state:paused}" +
    "@keyframes nsmk-roll{from{transform:translateX(0)}to{transform:translateX(-50%)}}" +
    ".nsmk-run .nsmk-vp{-webkit-mask-image:linear-gradient(90deg,transparent,#000 24px,#000 calc(100% - 24px),transparent);mask-image:linear-gradient(90deg,transparent,#000 24px,#000 calc(100% - 24px),transparent)}" +
    "@media (prefers-reduced-motion:reduce){.nsmk-run .nsmk-row{animation:none}.nsmk-vp{overflow-x:auto}}" +
    "@media (max-width:640px){.nsmk-in{gap:10px}.nsmk-lab{font-size:.66rem}}" +
    "@media print{.nsmk{display:none}}";

  function day(iso) {
    var p = String(iso || "").split("-");
    return p.length === 3 ? (+p[2]) + " " + MON[+p[1] - 1] : "";
  }

  function item(x) {
    var c = Number(x.c);
    var cls = c > 0.004 ? "nsmk-up" : c < -0.004 ? "nsmk-dn" : "nsmk-fl";
    var arrow = c > 0.004 ? "▲" : c < -0.004 ? "▼" : "▬";
    var sign = c > 0 ? "+" : c < 0 ? "−" : "";
    var span = document.createElement("span");
    span.className = "nsmk-it";
    var b = document.createElement("b");
    b.textContent = x.n;
    var v = document.createElement("span");
    v.className = cls;
    v.textContent = arrow + " " + sign + Math.abs(c).toFixed(2) + "%";
    v.setAttribute("aria-label", (c > 0 ? "up " : c < 0 ? "down " : "unchanged ") + Math.abs(c).toFixed(2) + " percent");
    span.appendChild(b);
    span.appendChild(v);
    return span;
  }

  function show(m) {
    if (!m || !m.items || !m.items.length || !m.date) return;
    var age = (Date.now() - new Date(m.date + "T18:00:00+05:30").getTime()) / 864e5;
    if (!(age < 8)) return;                                   // stale data: show nothing

    var st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);

    var bar = document.createElement("div");
    bar.className = "nsmk";
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "Market, day's change on " + day(m.date));
    var inner = document.createElement("div");
    inner.className = "nsmk-in";

    var lab = document.createElement("a");
    lab.className = "nsmk-lab";
    lab.href = EXPLORER;
    lab.textContent = "Day's change · " + day(m.date);
    lab.title = (m.src || "") + ". " + (m.note || "");

    var vp = document.createElement("div");
    vp.className = "nsmk-vp";
    var row = document.createElement("div");
    row.className = "nsmk-row";
    m.items.forEach(function (x) { row.appendChild(item(x)); });
    vp.appendChild(row);

    inner.appendChild(lab);
    inner.appendChild(vp);
    bar.appendChild(inner);

    var nav = document.querySelector("nav.ns-nav, nav.nav");
    var at = nav || document.querySelector("header");
    if (at) at.insertAdjacentElement("afterend", bar);
    else document.body.insertBefore(bar, document.body.firstChild);

    // Roll like a ticker only when the figures don't fit on one line (phones)
    function fit() {
      bar.classList.remove("nsmk-run");
      while (row.children.length > m.items.length) row.removeChild(row.lastChild);
      if (row.scrollWidth > vp.clientWidth + 2) {
        m.items.forEach(function (x) { var c = item(x); c.setAttribute("aria-hidden", "true"); row.appendChild(c); });
        bar.style.setProperty("--nsmk-t", Math.max(18, Math.round(row.scrollWidth / 2 / 40)) + "s");
        bar.classList.add("nsmk-run");
      }
    }
    fit();
    var t;
    window.addEventListener("resize", function () { clearTimeout(t); t = setTimeout(fit, 200); });
  }

  fetch(BASE + "data/market.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(show)
    .catch(function () { /* no strip if the data can't be loaded */ });
})();
