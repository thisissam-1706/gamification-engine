// Streakwise demo Admin shell. Exposes window.Admin for the per-milestone tab scripts.
(function () {
  "use strict";

  var PRIYA_ID = "4871b5d7-1673-5416-8a37-48353d80e6a4";
  var TABS = ["events", "cohorts", "policy", "notifications", "abuse", "experiments", "recovery"];
  var state = {
    learnerId: null, learners: [], tabFns: {}, ready: false,
    changeFns: [], learnerFns: [], version: null, clock: null
  };

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function pad2(n) { return String(n).padStart(2, "0"); }
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // All demo timestamps are UTC, matching the "Demo clock: ... UTC" label. Table cells stay compact;
  // pass withUtc where there is room (headings, sentences) to append " UTC".
  function fmtTime(iso, withUtc) {
    if (!iso) return "-";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + pad2(d.getUTCHours()) + ":" +
      pad2(d.getUTCMinutes()) + ":" + pad2(d.getUTCSeconds()) + (withUtc ? " UTC" : "");
  }
  // Strip a leading "<word>:" prefix (attempt:, quest:, ...) so truncated ids show distinct characters.
  function short(id) {
    if (!id) return "-";
    return String(id).replace(/^[A-Za-z_]+:/, "").slice(0, 8);
  }
  function badge(text, kind) {
    return '<span class="adm-badge adm-badge-' + esc(kind || "info") + '">' + esc(text) + "</span>";
  }
  function table(columns, rows) {
    var html = '<div class="adm-table-wrap"><table class="adm-table"><thead><tr>';
    columns.forEach(function (c) { html += "<th>" + esc(c.label) + "</th>"; });
    html += "</tr></thead><tbody>";
    rows.forEach(function (row) {
      html += "<tr>";
      columns.forEach(function (c) {
        var cell = c.render ? c.render(row) : esc(row[c.key]);
        html += "<td>" + cell + "</td>";
      });
      html += "</tr>";
    });
    html += "</tbody></table></div>";
    return html;
  }
  function toast(text, kind) {
    var box = $("adm-toasts");
    var node = document.createElement("div");
    node.className = "adm-toast" + (kind ? " adm-toast-" + kind : "");
    node.textContent = text;
    box.appendChild(node);
    setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 5000);
  }
  function runAll(fns) {
    fns.slice().forEach(function (fn) {
      try { fn(); } catch (e) { toast(e.message || String(e), "bad"); }
    });
  }
  function api(method, url, body) {
    var options = { method: method };
    if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var detail = data.detail;
          if (detail && typeof detail !== "string") detail = JSON.stringify(detail);
          throw new Error(detail || data.message || ("Request failed (" + response.status + ")"));
        }
        if (method !== "GET") runAll(state.changeFns);
        return data;
      });
    });
  }
  function learnerId() { return state.learnerId; }
  function learnerName(id) {
    for (var i = 0; i < state.learners.length; i++) {
      if (state.learners[i].learner_id === id) return state.learners[i].display_name;
    }
    return short(id);
  }
  function onChange(fn) { state.changeFns.push(fn); }
  function onLearnerChange(fn) { state.learnerFns.push(fn); }

  // ---- tabs
  function showTab(name) {
    if (TABS.indexOf(name) < 0) name = "events";
    TABS.forEach(function (t) {
      var section = $("tab-" + t);
      if (section) section.hidden = t !== name;
    });
    Array.prototype.forEach.call(document.querySelectorAll(".adm-tab"), function (btn) {
      var active = btn.getAttribute("data-tab") === name;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (location.hash !== "#" + name) {
      try { history.replaceState(null, "", "#" + name); } catch (e) { location.hash = name; }
    }
  }
  function tabFromHash() { return (location.hash || "").replace("#", "") || "events"; }
  function registerTab(name, fn) {
    state.tabFns[name] = fn;
    if (state.ready) initTab(name);
  }
  function initTab(name) {
    var section = $("tab-" + name);
    var root = section ? section.querySelector(".adm-body") : null;
    if (!root) return;
    try { state.tabFns[name](root); } catch (e) { toast(e.message || String(e), "bad"); }
  }

  // ---- clock
  function refreshClock() {
    if (!state.learnerId) return Promise.resolve();
    return fetch("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.clock = new Date(data.now_utc);
        $("adm-clock").textContent = "Demo clock: " + data.now_utc.replace("T", " ").replace("Z", " UTC");
        var dt = $("adm-dt");
        if (dt && document.activeElement !== dt) dt.value = data.now_utc.slice(0, 16);
      })
      .catch(function (e) { toast(e.message, "bad"); });
  }
  function currentClock() {
    return fetch("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock")
      .then(function (r) { return r.json(); })
      .then(function (data) { return new Date(data.now_utc); });
  }
  function setClock(date) {
    return api("POST", "/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock",
      { now_utc: date.toISOString() }).then(refreshClock).catch(function (e) { toast(e.message, "bad"); });
  }
  function moveClock(adjust) {
    return currentClock().then(function (clock) {
      adjust(clock);
      return setClock(clock);
    }).catch(function (e) { toast(e.message, "bad"); });
  }

  function setLearner(id, fromUser) {
    state.learnerId = id;
    try {
      var url = new URL(window.location.href);
      url.searchParams.set("learner", id);
      history.replaceState(null, "", url);
    } catch (e) { /* ignore */ }
    refreshClock();
    if (fromUser) runAll(state.learnerFns);
  }

  function loadLearners() {
    return api("GET", "/api/demo/personas").then(function (list) {
      state.learners = list;
      var select = $("adm-learner");
      select.innerHTML = "";
      var requested = new URLSearchParams(window.location.search).get("learner");
      var current = state.learnerId || requested;
      var ids = list.map(function (l) { return l.learner_id; });
      var chosen = ids.indexOf(current) >= 0 ? current :
        (ids.indexOf(PRIYA_ID) >= 0 ? PRIYA_ID : ids[0]);
      list.forEach(function (l) {
        var option = document.createElement("option");
        option.value = l.learner_id;
        option.textContent = l.display_name + " - " + l.role;
        option.selected = l.learner_id === chosen;
        select.appendChild(option);
      });
      setLearner(chosen, false);
    });
  }

  function pollVersion() {
    fetch("/api/demo/version").then(function (r) { return r.json(); }).then(function (data) {
      if (state.version !== null && data.version !== state.version) {
        state.version = data.version;
        refreshClock();
        runAll(state.changeFns);
      } else {
        state.version = data.version;
      }
    }).catch(function () { /* ignore transient poll errors */ });
  }

  // ---- engine tick
  function runTick() {
    var btn = $("adm-tick"), dialog = $("adm-tick-dialog");
    btn.disabled = true;
    var original = btn.textContent;
    btn.textContent = "Running...";
    api("POST", "/api/demo/admin/tick", { now: null }).then(function (data) {
      var s = data.summary;
      var html = "<h3>Engine tick at " + esc(fmtTime(data.now, true)) + "</h3>" +
        '<div class="adm-tiles">' +
        tickTile("New decisions", s.new_decisions) + tickTile("New signals", s.new_signals) +
        tickTile("New notifications", s.new_notifications) + "</div>" +
        table([
          { label: "Step", key: "name" },
          { label: "Status", render: function (r) { return r.ok ? badge("ok", "ok") : badge("error", "bad"); } },
          { label: "What it did", render: function (r) { return esc(r.headline); } }
        ], data.steps);
      html += '<div class="adm-details">';
      data.steps.forEach(function (r) {
        html += "<details><summary>" + esc(r.name) + " raw</summary><pre class=\"adm-json\">" +
          esc(JSON.stringify(r.raw, null, 2)) + "</pre></details>";
      });
      html += '</div><div class="adm-actions"><button class="btn-small" type="button" id="adm-tick-close">Close</button></div>';
      dialog.innerHTML = html;
      $("adm-tick-close").addEventListener("click", function () {
        if (dialog.close) dialog.close(); else dialog.removeAttribute("open");
      });
      if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    }).catch(function (e) { toast(e.message, "bad"); }).then(function () {
      btn.disabled = false;
      btn.textContent = original;
    });
  }
  function tickTile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + esc(label) +
      '</div><div class="adm-tile-value">' + esc(value) + "</div></div>";
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll(".adm-tab"), function (btn) {
      btn.addEventListener("click", function () { showTab(btn.getAttribute("data-tab")); });
    });
    window.addEventListener("hashchange", function () { showTab(tabFromHash()); });
    showTab(tabFromHash());

    $("adm-learner").addEventListener("change", function (e) { setLearner(e.target.value, true); });
    $("adm-plus1").addEventListener("click", function () {
      moveClock(function (c) { c.setUTCHours(c.getUTCHours() + 1); });
    });
    $("adm-plus6").addEventListener("click", function () {
      moveClock(function (c) { c.setUTCHours(c.getUTCHours() + 6); });
    });
    $("adm-night").addEventListener("click", function () {
      moveClock(function (c) { c.setUTCHours(23, 0, 0, 0); });
    });
    $("adm-morning").addEventListener("click", function () {
      moveClock(function (c) { c.setUTCDate(c.getUTCDate() + 1); c.setUTCHours(9, 0, 0, 0); });
    });
    $("adm-dt-set").addEventListener("click", function () {
      var value = $("adm-dt").value;
      if (!value) { toast("Pick a date and time first", "bad"); return; }
      var d = new Date(value + ":00Z");
      if (isNaN(d.getTime())) { toast("Invalid date", "bad"); return; }
      setClock(d);
    });
    $("adm-tick").addEventListener("click", runTick);
    $("adm-reset").addEventListener("click", function () {
      api("POST", "/api/demo/reset").then(loadLearners).then(function () {
        runAll(state.learnerFns);
        toast("Demo reset", "ok");
      }).catch(function (e) { toast(e.message, "bad"); });
    });

    loadLearners().then(function () {
      state.ready = true;
      TABS.forEach(function (name) { if (state.tabFns[name]) initTab(name); });
      fetch("/api/demo/version").then(function (r) { return r.json(); })
        .then(function (d) { state.version = d.version; }).catch(function () {});
      setInterval(pollVersion, 2000);
    }).catch(function (e) { toast(e.message, "bad"); });
  }

  window.Admin = {
    api: api, learnerId: learnerId, onLearnerChange: onLearnerChange, onChange: onChange,
    esc: esc, fmtTime: fmtTime, short: short, badge: badge, toast: toast, table: table,
    registerTab: registerTab, learnerName: learnerName, showTab: showTab,
    refreshClock: refreshClock,
    get learners() { return state.learners; }
  };

  document.addEventListener("DOMContentLoaded", init);
})();
