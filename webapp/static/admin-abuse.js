// Abuse tab (M6): live progress toward each rule, detection pass, flags with evidence, rules in force.
(function () {
  "use strict";

  var TYPE_KIND = { replay: "warn", impossible_timing: "bad", scripted_farming: "info", duplicate_exploit: "ok" };

  function tile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
      '</div><div class="adm-tile-value">' + Admin.esc(value) + "</div></div>";
  }

  Admin.registerTab("abuse", function (root) {
    var html = '<p>Detection is flag-only: it never changes XP, streaks or leaderboards. It records suspected abuse with evidence.</p>' +
      '<p class="adm-hint">Live detection is on: after every lesson submission the demo runs the flag-only pass for that learner ' +
      '(the stand-in for the engine tick\'s abuse step), so flags appear immediately. "Scan" below re-runs it on demand.</p>' +
      '<div class="adm-card"><h3>Live progress toward each rule</h3><div id="ab-live"></div>' +
      '<p class="adm-hint">Redo a finished lesson on the lesson page (Redo this lesson): 4 redos inside 24 h raise a scripted-farming flag.</p></div>' +
      '<div class="adm-card"><h3>Detection pass</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" data-scan="one">Scan selected learner</button>' +
      '<button class="btn-small" type="button" data-scan="all">Scan everyone</button></div><div id="ab-scan-out"></div></div>' +
      '<div class="adm-card"><h3>Flags</h3><div class="adm-row"><label for="ab-filter">Show</label>' +
      '<select id="ab-filter"><option value="one">Selected learner</option><option value="all">Everyone</option></select></div>' +
      '<div id="ab-flags"></div></div>' +
      '<details class="adm-card adm-details"><summary>Rules in force</summary><pre class="adm-json" id="ab-rules">Loading...</pre></details>';
    root.innerHTML = html;
    var token = 0;

    function meter(label, m, unit) {
      var pct = Math.min(100, Math.round(100 * m.count / Math.max(1, m.threshold)));
      var hot = m.count >= m.threshold;
      return '<div class="adm-meter' + (hot ? " is-hot" : "") + '"><div class="adm-meter-top"><span>' + Admin.esc(label) +
        "</span><strong>" + m.count + " / " + m.threshold + "</strong></div>" +
        '<div class="adm-meter-bar"><span style="width:' + pct + '%"></span></div>' +
        '<div class="adm-hint">' + Admin.esc(unit) + "</div></div>";
    }
    function renderLive() {
      return Admin.api("GET", "/api/demo/admin/learners/" + Admin.learnerId() + "/abuse/live").then(function (d) {
        var rules = Object.keys(d.flags.by_rule);
        root.querySelector("#ab-live").innerHTML = '<div class="adm-meters">' +
          meter("Repeated lessons (scripted farming)", d.repeats, "redos of already-completed lessons in the last " + d.repeats.window_hours + " h") +
          meter("Gaps under " + d.short_gaps.min_gap_seconds + " s (impossible timing)", d.short_gaps, "in the last " + d.short_gaps.window_minutes + " min") +
          meter("Completions in a burst (impossible timing)", d.burst, "in the last " + d.burst.window_minutes + " min") +
          "</div>" + '<p class="adm-hint">Last lesson: ' + Admin.esc(d.last_lesson_id || "none yet") + ". Flags for this learner: " +
          (rules.length ? rules.map(function (r) { return Admin.esc(r) + " x" + d.flags.by_rule[r]; }).join(", ") : "none") +
          ". Counted at the demo clock " + Admin.esc(Admin.fmtTime(d.clock, true)) + ".</p>" +
          (d.xp_hold ? '<div class="adm-strip">' + Admin.badge("XP on hold", "bad") +
            "<span>A scripted-farming flag from the last 24 h: new lessons are accepted but earn 0 XP until 24 h pass without farming.</span></div>" : "");
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    function renderFlags() {
      var t = ++token;
      var filter = root.querySelector("#ab-filter").value;
      var url = "/api/demo/admin/abuse/signals" + (filter === "one" ? "?learner_id=" + Admin.learnerId() : "");
      Admin.api("GET", url).then(function (d) {
        if (t !== token) return;
        var el = root.querySelector("#ab-flags");
        if (!d.signals.length) { el.innerHTML = '<p class="muted">No flags yet. Redo a lesson or finish lessons very quickly: flags appear here as soon as a rule is crossed.</p>'; return; }
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
            s.evidence.length + " completion" + (s.evidence.length === 1 ? "" : "s") +
            " shown: the run that first crossed the rule's threshold (each completion is referenced by its completion id and its event id, " +
            s.evidence_refs.length + " references in all). Later completions in the same episode are not added; a new episode gets its own flag.</p>" +
            ev + "</div>";
        }).join("");
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

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
      root.querySelector("#ab-scan-out").innerHTML = ""; renderFlags(); renderLive();
    });
    Admin.onChange(function () { renderFlags(); renderLive(); });
    renderFlags();
    renderLive();
  });
})();
