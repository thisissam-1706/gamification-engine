// Recovery tab (M8): leaderboard wipe and rebuild, dead-letter state, scale benchmark.
(function () {
  "use strict";

  function tile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
      '</div><div class="adm-tile-value">' + Admin.esc(value) + "</div></div>";
  }
  function fmtNum(n) {
    if (Math.floor(n) === n) return n.toLocaleString("en-US");
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function label(key) { return String(key).replace(/_/g, " "); }

  // Generic report renderer: nested objects become labelled rows, booleans become badges.
  function renderValue(v) {
    if (v === null || v === undefined) return "-";
    if (typeof v === "boolean") return Admin.badge(v ? "yes" : "no", v ? "ok" : "bad");
    if (typeof v === "number") return Admin.esc(fmtNum(v));
    if (Array.isArray(v)) {
      if (!v.length) return "none";
      if (v.every(function (x) { return typeof x !== "object" || x === null; })) return Admin.esc(v.join(", "));
      return Admin.esc(v.length + " items");
    }
    if (typeof v === "object") return renderRows(v);
    return Admin.esc(v);
  }
  function renderRows(obj) {
    var html = '<dl class="adm-kv">';
    Object.keys(obj).forEach(function (k) {
      html += "<dt>" + Admin.esc(label(k)) + "</dt><dd>" + renderValue(obj[k]) + "</dd>";
    });
    return html + "</dl>";
  }
  function renderReport(report, title) {
    var html = '<div class="adm-card"><h3>' + Admin.esc(title) + "</h3>";
    if (typeof report.overall_passed === "boolean") {
      html += '<div class="adm-banner ' + (report.overall_passed ? "adm-banner-ok" : "adm-banner-bad") + '" style="font-size:20px">Overall: ' +
        (report.overall_passed ? "PASSED" : "FAILED") + "</div>";
    }
    Object.keys(report).forEach(function (key) {
      if (key === "overall_passed") return;
      var v = report[key];
      html += '<div class="adm-card"><strong>' + Admin.esc(label(key)) + "</strong>" +
        (v !== null && typeof v === "object" && !Array.isArray(v) ? renderRows(v) : "<p>" + renderValue(v) + "</p>") + "</div>";
    });
    return html + "</div>";
  }

  Admin.registerTab("recovery", function (root) {
    root.innerHTML =
      '<div class="adm-card"><h3>Rebuild from the event log</h3>' +
      "<p>The leaderboard is a derived read model. Delete it and replay the event log: the checksum must match.</p>" +
      '<div class="adm-actions">' +
      '<button class="btn-small" type="button" data-rc="wipe">1. Wipe leaderboard</button>' +
      '<button class="btn-small" type="button" data-rc="rebuild">2. Rebuild from event log</button>' +
      '<button class="btn-small" type="button" data-rc="both">Wipe and rebuild</button></div>' +
      '<p class="adm-hint">Open the Learner home pane after step 1: the leaderboard is empty. After step 2 it is back.</p>' +
      '<div id="rc-out"></div></div>' +
      '<div class="adm-card"><h3>Notification recovery</h3><div id="rc-dlq"></div>' +
      '<p class="adm-hint">Outage demo: in the Notifications tab turn a provider outage on, send a push (it lands in the dead-letter queue), turn the outage off and replay dead letters.</p>' +
      '<p><a href="#notifications" id="rc-notif-link">Open Notifications tab</a></p></div>' +
      '<div class="adm-card"><h3>Scale benchmark</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" id="rc-bench">Run small benchmark live</button></div><div id="rc-bench-out"></div>' +
      '<div class="adm-row"><select id="rc-reports"></select><button class="btn-small" type="button" id="rc-show">Show saved report</button></div>' +
      '<div id="rc-saved-out"></div></div>';
    var out = root.querySelector("#rc-out");

    function fp(label, f) {
      return tile(label + " rows", f.rows) + tile(label + " checksum", f.hash.slice(0, 12));
    }
    function showWipe(w) {
      return '<div class="adm-tiles">' + fp("Before", w.before) + "</div><p>Leaderboard wiped.</p>";
    }
    function showRebuild(r) {
      var banner = r.identical
        ? '<div class="adm-banner adm-banner-ok">Checksum identical before and after</div>'
        : '<div class="adm-banner adm-banner-bad">Mismatch' + (r.before_hash ? "" : " (no checksum recorded: wipe first)") + "</div>";
      return banner + '<div class="adm-tiles">' + fp("After", r.after) + tile("Events replayed", r.events_replayed) +
        tile("Duration (ms)", r.duration_ms) + tile("Events/sec", fmtNum(r.events_per_sec)) + "</div>";
    }
    Array.prototype.forEach.call(root.querySelectorAll("button[data-rc]"), function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-rc"), p;
        btn.disabled = true;
        if (kind === "wipe") p = Admin.api("POST", "/api/demo/admin/recovery/wipe-leaderboard", {}).then(function (w) { out.innerHTML = showWipe(w); });
        else if (kind === "rebuild") p = Admin.api("POST", "/api/demo/admin/recovery/rebuild-leaderboard", {}).then(function (r) { out.innerHTML = showRebuild(r); });
        else p = Admin.api("POST", "/api/demo/admin/recovery/wipe-and-rebuild", {}).then(function (d) {
          out.innerHTML = showWipe(d.wipe) + showRebuild(d.rebuild);
        });
        p.catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () { btn.disabled = false; });
      });
    });

    function loadDlq() {
      Admin.api("GET", "/api/demo/admin/recovery/dead-letters").then(function (d) {
        root.querySelector("#rc-dlq").innerHTML = '<div class="adm-tiles">' + tile("Open dead letters", d.open) + tile("Resolved", d.resolved) + "</div>" +
          (d.entries.length ? Admin.table([
            { label: "Learner", key: "learner" }, { label: "Channel", key: "channel" },
            { label: "Reason", key: "reason" }, { label: "Attempts", key: "attempts" },
            { label: "Failed", render: function (e) { return Admin.esc(Admin.fmtTime(e.failed_at)); } },
            { label: "Resolved", render: function (e) { return e.resolved ? Admin.badge("resolved", "ok") : Admin.badge("open", "bad"); } }
          ], d.entries) : '<p class="muted">No dead letters.</p>');
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }
    root.querySelector("#rc-notif-link").addEventListener("click", function (ev) {
      ev.preventDefault();
      Admin.showTab("notifications");
    });

    root.querySelector("#rc-bench").addEventListener("click", function () {
      var btn = this, outEl = root.querySelector("#rc-bench-out");
      btn.disabled = true; btn.textContent = "Running...";
      Admin.api("POST", "/api/demo/admin/recovery/benchmark", { profile: "small" }).then(function (rep) {
        outEl.innerHTML = renderReport(rep, "Small benchmark (live)");
      }).catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () {
        btn.disabled = false; btn.textContent = "Run small benchmark live";
      });
    });
    var reportSel = root.querySelector("#rc-reports");
    Admin.api("GET", "/api/demo/admin/recovery/reports").then(function (list) {
      reportSel.innerHTML = "";
      if (!list.length) {
        var o = document.createElement("option"); o.textContent = "No saved reports"; o.value = ""; reportSel.appendChild(o);
      }
      list.forEach(function (r) {
        var o = document.createElement("option");
        o.value = r.name; o.textContent = r.name + " (" + Admin.fmtTime(r.modified) + ")";
        reportSel.appendChild(o);
      });
    }).catch(function (e) { Admin.toast(e.message, "bad"); });
    root.querySelector("#rc-show").addEventListener("click", function () {
      var name = reportSel.value;
      if (!name) return;
      var text = reportSel.options[reportSel.selectedIndex].textContent;
      Admin.api("GET", "/api/demo/admin/recovery/reports/" + encodeURIComponent(name)).then(function (rep) {
        root.querySelector("#rc-saved-out").innerHTML = renderReport(rep, text);
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    });

    Admin.onChange(loadDlq);
    loadDlq();
  });
})();
