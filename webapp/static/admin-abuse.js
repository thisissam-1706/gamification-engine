// Abuse tab (M6): attack simulator, detection pass, flags with evidence, rules in force.
(function () {
  "use strict";

  var ATTACKS = [
    { key: "replay", label: "Replay attack", text: "6 accepted lessons, each re-sent once unchanged." },
    { key: "impossible_timing", label: "Impossible timing", text: "6 lessons 5 seconds apart (minimum is 20 s)." },
    { key: "scripted_farming", label: "Scripted point farming", text: "14 lessons exactly 90 seconds apart, like a script." },
    { key: "duplicate_exploit", label: "Duplicate-event exploit", text: "4 completions re-sent under new event ids." }
  ];
  var TYPE_KIND = { replay: "warn", impossible_timing: "bad", scripted_farming: "info", duplicate_exploit: "ok" };

  function tile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
      '</div><div class="adm-tile-value">' + Admin.esc(value) + "</div></div>";
  }

  Admin.registerTab("abuse", function (root) {
    var html = '<p>Detection is flag-only: it never changes XP, streaks or leaderboards. It records suspected abuse with evidence.</p>' +
      '<div class="adm-card"><h3>Attack simulator</h3><div class="adm-grid">';
    ATTACKS.forEach(function (a) {
      html += '<div><button class="btn-small" type="button" data-attack="' + a.key + '">' + a.label +
        '</button><p class="adm-hint">' + a.text + "</p></div>";
    });
    html += '</div><div id="ab-attack-out"></div></div>' +
      '<div class="adm-card"><h3>Detection pass</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" data-scan="one">Scan selected learner</button>' +
      '<button class="btn-small" type="button" data-scan="all">Scan everyone</button></div><div id="ab-scan-out"></div></div>' +
      '<div class="adm-card"><h3>Flags</h3><div class="adm-row"><label for="ab-filter">Show</label>' +
      '<select id="ab-filter"><option value="one">Selected learner</option><option value="all">Everyone</option></select></div>' +
      '<div id="ab-flags"></div></div>' +
      '<details class="adm-card adm-details"><summary>Rules in force</summary><pre class="adm-json" id="ab-rules">Loading...</pre></details>';
    root.innerHTML = html;
    var token = 0;

    function renderFlags() {
      var t = ++token;
      var filter = root.querySelector("#ab-filter").value;
      var url = "/api/demo/admin/abuse/signals" + (filter === "one" ? "?learner_id=" + Admin.learnerId() : "");
      Admin.api("GET", url).then(function (d) {
        if (t !== token) return;
        var el = root.querySelector("#ab-flags");
        if (!d.signals.length) { el.innerHTML = '<p class="muted">No flags yet. Run an attack, then a detection pass.</p>'; return; }
        el.innerHTML = d.signals.map(function (s) {
          var ev = s.evidence.length ? Admin.table([
            { label: "Time", render: function (e) { return Admin.esc(e.time ? Admin.fmtTime(e.time) : "-"); } },
            { label: "Lesson", render: function (e) { return Admin.esc(e.lesson_id || "-"); } },
            { label: "Reason", render: function (e) { return Admin.esc(e.resolved ? e.reason : "unresolved: " + e.ref); } },
            { label: "Event", render: function (e) { return Admin.esc(e.event_id ? Admin.short(e.event_id) : "-"); } }
          ], s.evidence) : "";
          return '<div class="adm-card">' + Admin.badge(s.signal_type, TYPE_KIND[s.signal_type] || "info") + " " +
            Admin.badge(s.status, "warn") + " <strong>" + Admin.esc(s.display_name) + "</strong>" +
            '<p class="adm-hint">Rule ' + Admin.esc(s.rule_id) + " (" + Admin.esc(s.rule_version) + "). Window " +
            Admin.esc(Admin.fmtTime(s.window_start)) + " to " + Admin.esc(Admin.fmtTime(s.window_end)) + ". " +
            s.evidence_refs.length + " evidence references.</p>" + ev + "</div>";
        }).join("");
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    Array.prototype.forEach.call(root.querySelectorAll("button[data-attack]"), function (btn) {
      btn.addEventListener("click", function () {
        btn.disabled = true;
        Admin.api("POST", "/api/demo/admin/learners/" + Admin.learnerId() + "/abuse/attack", { pattern: btn.getAttribute("data-attack") })
          .then(function (d) {
            var reasons = Object.keys(d.rejected_by_reason).map(function (r) { return r + " " + d.rejected_by_reason[r]; }).join(", ");
            root.querySelector("#ab-attack-out").innerHTML = '<div class="adm-strip"><span>' + d.events_sent + " events sent, " +
              d.accepted + " accepted" + (reasons ? ", rejected: " + Admin.esc(reasons) : "") + "</span><span>XP " +
              d.before.xp_total + " -> " + d.after.xp_total + "</span></div>";
          }).catch(function (e) { Admin.toast(e.message, "bad"); })
          .then(function () { btn.disabled = false; });
      });
    });
    Array.prototype.forEach.call(root.querySelectorAll("button[data-scan]"), function (btn) {
      btn.addEventListener("click", function () {
        btn.disabled = true;
        var body = { learner_id: btn.getAttribute("data-scan") === "one" ? Admin.learnerId() : null };
        Admin.api("POST", "/api/demo/admin/abuse/scan", body).then(function (d) {
          var same = !d.scores_changed && d.event_log_before === d.event_log_after;
          root.querySelector("#ab-scan-out").innerHTML = '<div class="adm-tiles">' + tile("New signals", d.summary.new_signals) +
            tile("Events read", d.summary.events_read) + tile("Learners scanned", d.summary.learners_scanned) + "</div>" +
            '<div class="adm-banner ' + (same ? "adm-banner-ok" : "adm-banner-bad") + '">Scores changed by this scan: ' +
            (d.scores_changed ? "YES" : "no") + ". Event log entries before/after: " + d.event_log_before + "/" + d.event_log_after + ".</div>";
        }).catch(function (e) { Admin.toast(e.message, "bad"); })
          .then(function () { btn.disabled = false; });
      });
    });
    root.querySelector("#ab-filter").addEventListener("change", renderFlags);
    Admin.api("GET", "/api/demo/admin/abuse/rules").then(function (r) {
      root.querySelector("#ab-rules").textContent = "version " + r.version + "\n" + JSON.stringify(r.rules, null, 2);
    }).catch(function (e) { Admin.toast(e.message, "bad"); });

    Admin.onLearnerChange(function () {
      root.querySelector("#ab-attack-out").innerHTML = ""; root.querySelector("#ab-scan-out").innerHTML = ""; renderFlags();
    });
    Admin.onChange(renderFlags);
    renderFlags();
  });
})();
