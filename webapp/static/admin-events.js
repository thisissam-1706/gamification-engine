// Events tab (M1/M2): immutable event history, ingestion decisions, and the replay lab.
(function () {
  "use strict";

  var TYPE_BADGES = { XPAwarded: "ok", StreakUpdated: "info", BadgeAwarded: "ok", QuestCompleted: "ok" };
  // Key facts per event type: the most meaningful fields from src/state_machine/learner.py.
  var KEY_FIELDS = {
    LessonCompleted: ["lesson_id", "activity_date"],
    XPAwarded: ["xp_amount", "completion_kind", "rule_version"],
    StreakUpdated: ["new_streak", "previous_streak", "freezes_consumed"],
    BadgeAwarded: ["badge_id"],
    QuestAssigned: ["quest_id", "deadline_at"],
    QuestProgressed: ["quest_id", "current_step", "target_step"],
    QuestCompleted: ["quest_id"],
    QuestExpired: ["quest_id"],
    CheckpointAttempted: ["assessment_id", "passed"],
    RemediationDecided: ["action", "reason"],
    CohortAssigned: ["cohort_id", "membership_epoch"],
    FreezeAcquired: ["freeze_id"],
    ExperimentAssigned: ["exp_id", "variant"],
    ExperimentExposed: ["exp_id", "view_id"]
  };
  var SHORT_KEYS = { xp_amount: "XP", new_streak: "streak", previous_streak: "was" };

  function keyFacts(ev) {
    var names = KEY_FIELDS[ev.type] || [];
    var out = [];
    names.forEach(function (name) {
      var value = ev.fields[name];
      if (value === null || value === undefined) return;
      if (name === "xp_amount") out.push("+" + value + " XP");
      else out.push((SHORT_KEYS[name] || name) + ": " + value);
    });
    return out.slice(0, 3).join(", ");
  }


  // ---- M2 lab: duplicate, late events, rebuild-and-compare, rule version
  function buildLab(slot) {
    slot.innerHTML =
      '<div class="adm-card"><h3>Replay and late-event lab</h3>' +
      '<p class="adm-hint">Pick the replay/late-event learner (Ishaan Gupta) to keep the story clean.</p>' +
      '<div class="adm-actions">' +
      '<button class="btn-small" type="button" data-lab="fresh-lesson">Send a lesson</button>' +
      '<button class="btn-small" type="button" data-lab="duplicate">Resend the last event (exact duplicate)</button>' +
      '<button class="btn-small" type="button" data-lab="late" data-hours="24">Send event 24 h late (inside 72 h window)</button>' +
      '<button class="btn-small" type="button" data-lab="late" data-hours="100">Send event 100 h late (outside window)</button>' +
      '</div><div id="lab-result"></div>' +
      '<div class="adm-actions"><button class="btn-small" type="button" id="lab-rebuild">Rebuild this learner from history and compare</button></div>' +
      '<div id="lab-rebuild-out"></div>' +
      '<div class="adm-row"><strong>Rule version</strong><select id="lab-ruleset"></select>' +
      '<button class="btn-small" type="button" id="lab-ruleset-apply">Apply to new events</button></div>' +
      '<p class="adm-hint">Old rewards keep the version they were issued under; only events sent from now on use the new one.</p>' +
      '</div>';
    var resultEl = slot.querySelector("#lab-result");
    var rebuildEl = slot.querySelector("#lab-rebuild-out");
    var rulesetSel = slot.querySelector("#lab-ruleset");

    function base() { return "/api/demo/admin/learners/" + Admin.learnerId() + "/lab/"; }
    function delta(a, b) {
      var d = b - a;
      return (d >= 0 ? "+" : "") + d;
    }
    function showResult(data, label) {
      var r = data.result;
      var kind = r.status === "accepted" ? "ok" : "warn";
      var html = '<div class="adm-strip">' + Admin.badge(r.status, kind) +
        "<span>" + Admin.esc(r.reason || "no rejection reason") + "</span>" +
        "<span>XP " + data.before.xp_total + " -> " + data.after.xp_total + " (" +
        delta(data.before.xp_total, data.after.xp_total) + ")</span>" +
        "<span>Streak " + data.before.streak + " -> " + data.after.streak + "</span>";
      if (label) html += '<span class="muted">' + Admin.esc(label) + "</span>";
      resultEl.innerHTML = html + "</div>";
    }
    Array.prototype.forEach.call(slot.querySelectorAll("button[data-lab]"), function (btn) {
      btn.addEventListener("click", function () {
        var action = btn.getAttribute("data-lab");
        var hours = btn.getAttribute("data-hours");
        var body = hours ? { hours_late: Number(hours) } : {};
        btn.disabled = true;
        Admin.api("POST", base() + action, body).then(function (data) {
          showResult(data, hours ? hours + " h late" : "");
        }).catch(function (e) { Admin.toast(e.message, "bad"); })
          .then(function () { btn.disabled = false; });
      });
    });
    slot.querySelector("#lab-rebuild").addEventListener("click", function () {
      Admin.api("POST", base() + "rebuild-compare", {}).then(function (data) {
        function show(v) { return Array.isArray(v) ? (v.length ? v.join(", ") : "none") : String(v); }
        var html = Admin.table([
          { label: "Field", key: "field" },
          { label: "Live", render: function (r) { return Admin.esc(show(r.live)); } },
          { label: "Rebuilt from events", render: function (r) { return Admin.esc(show(r.rebuilt)); } },
          { label: "Match", render: function (r) { return r.match ? Admin.badge("✓", "ok") : Admin.badge("✗", "bad"); } }
        ], data.rows);
        var banner = data.identical
          ? '<div class="adm-banner adm-banner-ok"><strong>Rebuilt state is identical to live state</strong> (' + data.events_replayed + " events replayed)</div>"
          : '<div class="adm-banner adm-banner-bad"><strong>Rebuilt state differs from live state</strong></div>';
        rebuildEl.innerHTML = banner + html;
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    });
    function loadRuleset() {
      Admin.api("GET", base() + "ruleset").then(function (data) {
        rulesetSel.innerHTML = "";
        data.available.forEach(function (v) {
          var o = document.createElement("option");
          o.value = v; o.textContent = v; o.selected = v === data.effective;
          rulesetSel.appendChild(o);
        });
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }
    slot.querySelector("#lab-ruleset-apply").addEventListener("click", function () {
      Admin.api("POST", base() + "ruleset", { version: rulesetSel.value }).then(function (data) {
        Admin.toast("New events will use rule version " + data.effective, "ok");
        loadRuleset();
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    });
    Admin.onLearnerChange(function () { resultEl.innerHTML = ""; rebuildEl.innerHTML = ""; loadRuleset(); });
    loadRuleset();
  }

  Admin.registerTab("events", function (root) {
    root.innerHTML =
      '<p>Every row below is an immutable event. XP, streaks, quests and badges on the learner home page are a fold of these rows.</p>' +
      '<div class="adm-tiles" id="ev-snapshot"></div>' +
      '<div id="lab-slot"></div>' +
      '<div class="adm-grid">' +
      '<div><div class="adm-card"><h3>Event history</h3><div id="ev-history"></div></div>' +
      '<div class="adm-card" id="ev-detail" hidden></div></div>' +
      '<div class="adm-card"><h3>Ingestion decisions</h3><div id="ev-ingest"></div></div>' +
      '</div>';

    var snapshotEl = root.querySelector("#ev-snapshot");
    var historyEl = root.querySelector("#ev-history");
    var detailEl = root.querySelector("#ev-detail");
    var ingestEl = root.querySelector("#ev-ingest");
    var current = { events: [], selected: null, token: 0 };

    function tile(label, value, small) {
      return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
        '</div><div class="adm-tile-value' + (small ? " small" : "") + '">' + Admin.esc(value) + "</div></div>";
    }

    function renderDetail() {
      var ev = null;
      current.events.forEach(function (e) { if (e.seq === current.selected) ev = e; });
      Array.prototype.forEach.call(historyEl.querySelectorAll("tbody tr"), function (tr) {
        var cid = tr.getAttribute("data-cid");
        tr.classList.toggle("adm-row-linked", !!ev && !!ev.correlation_id && cid === ev.correlation_id);
      });
      if (!ev) { detailEl.hidden = true; return; }
      var shared = 0;
      current.events.forEach(function (e) {
        if (ev.correlation_id && e.correlation_id === ev.correlation_id) shared++;
      });
      detailEl.hidden = false;
      detailEl.innerHTML = "<h3>" + Admin.esc(ev.type) + " #" + ev.seq + "</h3>" +
        (ev.correlation_id
          ? '<p class="adm-hint">' + shared + " events share this correlation id</p>"
          : '<p class="adm-hint">This event has no correlation id.</p>') +
        '<pre class="adm-json">' + Admin.esc(JSON.stringify(ev, null, 2)) + "</pre>";
    }

    function renderHistory(data) {
      current.events = data.events;
      var html = '<p class="adm-hint">' + data.total + " events in this learner's stream; newest " +
        data.events.length + " shown. Click a row for details.</p>";
      html += Admin.table([
        { label: "#", key: "seq" },
        { label: "Time", render: function (e) { return Admin.esc(Admin.fmtTime(e.timestamp)); } },
        { label: "Type", render: function (e) {
          return TYPE_BADGES[e.type] ? Admin.badge(e.type, TYPE_BADGES[e.type]) : Admin.esc(e.type);
        } },
        { label: "Key facts", render: function (e) { return Admin.esc(keyFacts(e)); } },
        { label: "Correlation", render: function (e) {
          return e.correlation_id ? Admin.esc(Admin.short(e.correlation_id)) : "-";
        } }
      ], data.events);
      historyEl.innerHTML = html;
      Array.prototype.forEach.call(historyEl.querySelectorAll("tbody tr"), function (tr, i) {
        tr.className = "clickable";
        tr.setAttribute("data-cid", data.events[i].correlation_id || "");
        tr.addEventListener("click", function () {
          current.selected = data.events[i].seq;
          renderDetail();
        });
      });
      renderDetail();
    }

    function renderIngest(data) {
      var html = "";
      var reasons = Object.keys(data.by_reason);
      if (reasons.length) {
        html += '<div class="adm-chips">' + reasons.map(function (r) {
          return Admin.badge(r + " x" + data.by_reason[r], reasonKind(r));
        }).join("") + "</div>";
        html += Admin.table([
          { label: "Time", render: function (r) { return Admin.esc(Admin.fmtTime(r.received_at)); } },
          { label: "Reason", render: function (r) { return Admin.badge(r.reason, reasonKind(r.reason)); } },
          { label: "Event id", render: function (r) { return Admin.esc(Admin.short(r.event_id)); } },
          { label: "Action id", render: function (r) { return Admin.esc(r.action_id ? Admin.short(r.action_id) : "-"); } }
        ], data.records);
      } else {
        html = '<p class="muted">No rejected events for this learner yet.</p>';
      }
      ingestEl.innerHTML = html;
    }
    function reasonKind(reason) { return /duplicate/.test(reason) ? "warn" : "bad"; }

    function renderSnapshot(s) {
      snapshotEl.innerHTML = tile("XP", s.xp_total) + tile("Streak", s.streak) +
        tile("Freezes", s.freezes) + tile("Active days", s.active_days) + tile("Ruleset", s.ruleset);
    }

    function refresh() {
      var id = Admin.learnerId();
      var token = ++current.token;
      Promise.all([
        Admin.api("GET", "/api/demo/admin/learners/" + id + "/snapshot"),
        Admin.api("GET", "/api/demo/admin/learners/" + id + "/events?limit=60"),
        Admin.api("GET", "/api/demo/admin/learners/" + id + "/ingestion")
      ]).then(function (res) {
        if (token !== current.token) return;
        renderSnapshot(res[0]);
        renderHistory(res[1]);
        renderIngest(res[2]);
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    Admin.onLearnerChange(function () { current.selected = null; refresh(); });
    Admin.onChange(refresh);
    refresh();

    buildLab(root.querySelector("#lab-slot"));
  });
})();
