// Experiments tab (M7): guided exp_007 flow on the sandbox's real learner log.
(function () {
  "use strict";

  var REASON_TEXT = {
    not_treatment: "control learner: held out, no reminder",
    already_active_today: "skipped: already practised today",
    streak_already_broken: "skipped: the streak is already broken",
    no_streak: "skipped: no streak to protect",
    quiet_hours: "skipped: inside quiet hours",
    nothing_due: "skipped: no reminder time has been reached yet",
    missing_preferences: "skipped: no notification preferences",
    excluded_tenure: "skipped: learner is too new for the experiment",
    excluded_opted_out: "skipped: learner opted out of push or streak reminders"
  };
  var INVALID_TEXT = {
    no_eligible_learners: "no learner is eligible yet (assign first)",
    cap_breach_treatment: "the treatment arm broke the notification cap",
    srm_assigned: "assignment split is lopsided (sample ratio mismatch)",
    srm_eligible: "eligible split is lopsided (sample ratio mismatch)",
    missing_eligibility_records: "some learners have no eligibility record",
    mixed_assessment_versions: "attempts span more than one assessment version",
    delivered_over_cap_treatment: "treatment learners received more messages than the cap",
    gaming_share_breach: "too much XP came from repeated lessons (gaming)"
  };

  function tile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
      '</div><div class="adm-tile-value">' + Admin.esc(value) + "</div></div>";
  }
  function num(v, digits) {
    if (v === null || v === undefined) return "-";
    return typeof v === "number" ? (Math.round(v * Math.pow(10, digits || 3)) / Math.pow(10, digits || 3)) : String(v);
  }
  function reasonText(reason) {
    if (!reason) return "treatment learner at risk: reminder sent";
    if (reason.indexOf("ineligible:") === 0) return REASON_TEXT[reason.slice(11)] || reason;
    return REASON_TEXT[reason] || reason;
  }

  Admin.registerTab("experiments", function (root) {
    root.innerHTML =
      '<div class="adm-card"><p>Experiment exp_007 compares streak-risk reminders (treatment) with no reminder (control). ' +
      "The primary objective is learning progress (checkpoint mastery), not opens or clicks. " +
      "Guardrails: notification cap breaches and abuse-gaming share.</p></div>" +
      '<div class="adm-card"><h3>Step 1: Assign everyone</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" id="ex-assign">Assign all learners (deterministic)</button></div>' +
      '<div id="ex-assign-out"></div><p class="adm-hint">Assignment is a hash of learner id and experiment id, so re-running changes nothing.</p></div>' +
      '<div class="adm-card"><h3>Step 2: Who got what</h3><div id="ex-who"></div></div>' +
      '<div class="adm-card"><h3>Step 3: Trigger the reminder sweep for the selected learner</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" id="ex-sweep">Run streak-risk sweep now</button></div><div id="ex-sweep-out"></div>' +
      '<p class="adm-hint">Move the clock to the evening of a day the learner has not completed a lesson first.</p></div>' +
      '<div class="adm-card"><h3>Step 4: Experiment report</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" id="ex-report">Compute report</button></div><div id="ex-report-out"></div>' +
      '<p class="adm-hint" id="ex-report-note">The live sandbox starts with immature outcomes: no learner has a matured ' +
      'checkpoint result yet, so mastery shows as n/a here. See the reference run below for the mastery numbers.</p></div>' +
      '<div class="adm-card" id="ex-ref-card"><h3>Reference run (seeded population with matured outcomes)</h3>' +
      '<p class="adm-hint">The same exp_007 report code, computed over the saved small seed42 benchmark population ' +
      '(20 learners) instead of the live sandbox. It does not read or change the live log.</p>' +
      '<div class="adm-actions"><button class="btn-small" type="button" id="ex-ref">Compute reference report</button></div>' +
      '<div id="ex-ref-out"></div></div>' +
      '<div class="adm-card"><h3>Step 5: Reproducibility</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" id="ex-repro">Compute the report twice</button></div><div id="ex-repro-out"></div></div>';
    var token = 0;

    function loadAssignments() {
      var t = ++token;
      Admin.api("GET", "/api/demo/admin/experiments/assignments").then(function (d) {
        if (t !== token) return;
        var me = Admin.learnerId();
        var table = Admin.table([
          { label: "Learner", key: "display_name" },
          { label: "Arm", render: function (r) {
            return r.arm ? Admin.badge(r.arm, r.arm === "treatment" ? "info" : "warn") : "-";
          } },
          { label: "Eligible", render: function (r) {
            return r.eligible === null ? "-" : (r.eligible ? "yes" : "no" + (r.exclusion_reason ? " (" + Admin.esc(r.exclusion_reason) + ")" : ""));
          } },
          { label: "First exposure", render: function (r) {
            return r.first_exposure ? Admin.esc(Admin.fmtTime(r.first_exposure.exposed_at)) : "-";
          } }
        ], d.rows);
        var parts = table.split("<tr>");
        var out = parts[0] + "<tr>" + parts[1];
        for (var i = 2; i < parts.length; i++) {
          out += (d.rows[i - 2].learner_id === me ? '<tr class="adm-row-me">' : "<tr>") + parts[i];
        }
        root.querySelector("#ex-who").innerHTML = '<p class="adm-hint">' + d.assigned_count + " assigned (treatment " +
          d.arm_counts.treatment + ", control " + d.arm_counts.control + ").</p>" + out;
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    function busy(btn, promise) {
      btn.disabled = true;
      promise.catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () { btn.disabled = false; });
    }

    root.querySelector("#ex-assign").addEventListener("click", function () {
      busy(this, Admin.api("POST", "/api/demo/admin/experiments/assign", {}).then(function (d) {
        root.querySelector("#ex-assign-out").innerHTML = '<div class="adm-tiles">' + tile("Newly assigned", d.newly_assigned) +
          tile("Already assigned", d.already_assigned) + tile("Eligible", d.eligible) + tile("Excluded", d.excluded) + "</div>";
      }));
    });
    root.querySelector("#ex-sweep").addEventListener("click", function () {
      busy(this, Admin.api("POST", "/api/demo/admin/experiments/sweep", { learner_id: Admin.learnerId() }).then(function (d) {
        var o = d.outcomes[0] || {};
        var sent = !o.reason;
        root.querySelector("#ex-sweep-out").innerHTML = '<div class="adm-strip">' + Admin.badge(sent ? "sent" : "no reminder", sent ? "ok" : "warn") +
          "<span>" + Admin.esc(reasonText(o.reason)) + "</span>" + (o.fact_status ? "<span>status: " + Admin.esc(o.fact_status) + "</span>" : "") +
          '<span class="muted">at ' + Admin.esc(Admin.fmtTime(d.now, true)) + "</span></div>";
      }));
    });

    function renderReport(data, into) {
      var r = data.report;
      var html = r.valid
        ? '<div class="adm-banner adm-banner-ok">Valid experiment run</div>'
        : '<div class="adm-banner adm-banner-bad">Invalid run: ' + Admin.esc(r.invalid_reasons.map(function (x) { return INVALID_TEXT[x] || x; }).join("; ")) + "</div>";
      var rows = ["treatment", "control"].map(function (name) {
        var a = r.arms[name], m = a.mastery;
        return { arm: name, a: a, m: m };
      });
      html += Admin.table([
        { label: "Arm", render: function (x) { return Admin.badge(x.arm, x.arm === "treatment" ? "info" : "warn"); } },
        { label: "Eligible", render: function (x) { return Admin.esc(x.a.eligible); } },
        { label: "Mastery", render: function (x) {
          var m = x.m;
          return Admin.esc(m.rate === null ? "n/a (denominator " + m.denominator + ", immature " + m.immature + ")" : num(m.rate) + " (" + m.mastered + "/" + m.denominator + ")");
        } },
        { label: "Exposure rate", render: function (x) { return Admin.esc(x.a.exposure.rate === null ? (x.a.exposure.note ? "n/a (control has no surface)" : "-") : num(x.a.exposure.rate)); } },
        { label: "Msgs / learner / day", render: function (x) { return Admin.esc(num(x.a.messages_per_learner_per_day)); } },
        { label: "Max in 24 h", render: function (x) { return Admin.esc(x.a.max_delivered_in_24h); } },
        { label: "Cap breach learners", render: function (x) { return Admin.esc(x.a.delivered_cap_breach_learners); } },
        { label: "Opt-outs", render: function (x) { return Admin.esc(x.a.opt_outs === null ? "-" : x.a.opt_outs); } }
      ], rows);
      function srm(label, s) {
        return "<li>" + Admin.esc(label) + ": " + s.assigned_variant_a + " vs " + s.assigned_variant_b + ", p = " + num(s.p_value) +
          " " + (s.alert ? Admin.badge("mismatch", "bad") : Admin.badge("ok", "ok")) + "</li>";
      }
      html += '<h3 style="margin-top:12px">Sample-ratio checks</h3><ul class="adm-bullets">' + srm("Assigned", r.srm_assigned) + srm("Eligible", r.srm_eligible) + "</ul>";
      html += '<h3>Gaming guardrail</h3><p>' + (r.gaming.breached ? Admin.badge("breached", "bad") : Admin.badge("not breached", "ok")) + " " +
        Admin.esc(Object.keys(r.gaming.weeks).map(function (w) {
          return w + ": " + r.gaming.weeks[w].map(function (g) { return g.cohort_id + " share " + num(g.share); }).join(", ");
        }).join(" | ")) + "</p>";
      html += '<h3>Contrast (treatment vs control)</h3><dl class="adm-kv">' +
        "<dt>Mastery rate difference</dt><dd>" + Admin.esc(num(r.contrast.mastery_rate_difference)) + "</dd>" +
        "<dt>Mastery 95% CI</dt><dd>" + Admin.esc(r.contrast.mastery_ci95 ? JSON.stringify(r.contrast.mastery_ci95) : "-") + "</dd>" +
        "<dt>Messages/learner/day difference</dt><dd>" + Admin.esc(num(r.contrast.messages_per_learner_per_day_difference)) + "</dd></dl>";
      if (r.notes && r.notes.length) html += '<ul class="adm-bullets">' + r.notes.map(function (n) { return "<li>" + Admin.esc(n) + "</li>"; }).join("") + "</ul>";
      html += '<p class="adm-hint">Report hash: ' + Admin.esc(data.sha256.slice(0, 12)) + "</p>" +
        '<details class="adm-details"><summary>Raw report JSON</summary><pre class="adm-json">' + Admin.esc(JSON.stringify(r, null, 2)) + "</pre></details>";
      into.innerHTML = html;
    }
    root.querySelector("#ex-report").addEventListener("click", function () {
      busy(this, Admin.api("GET", "/api/demo/admin/experiments/report").then(function (d) {
        renderReport(d, root.querySelector("#ex-report-out"));
      }));
    });
    root.querySelector("#ex-ref").addEventListener("click", function () {
      busy(this, Admin.api("GET", "/api/demo/admin/experiments/reference-report").then(function (d) {
        var out = root.querySelector("#ex-ref-out");
        renderReport(d, out);
        out.insertAdjacentHTML("afterbegin", '<p class="adm-hint">Reference population: ' + Admin.esc(d.population.profile) +
          " profile, seed " + d.population.seed + ", " + d.population.learners + " learners, " + d.population.attempts +
          " checkpoint attempts, as of " + Admin.esc(Admin.fmtTime(d.as_of)) + " UTC.</p>");
      }));
    });
    root.querySelector("#ex-repro").addEventListener("click", function () {
      busy(this, Admin.api("POST", "/api/demo/admin/experiments/reproduce", {}).then(function (d) {
        root.querySelector("#ex-repro-out").innerHTML = '<div class="adm-banner ' + (d.identical ? "adm-banner-ok" : "adm-banner-bad") + '">' +
          (d.identical ? "Identical" : "Different") + '</div><p class="adm-hint">Run 1: ' + Admin.esc(d.hash_1.slice(0, 16)) +
          " | Run 2: " + Admin.esc(d.hash_2.slice(0, 16)) + "</p>";
      }));
    });

    Admin.onLearnerChange(function () { root.querySelector("#ex-sweep-out").innerHTML = ""; loadAssignments(); });
    Admin.onChange(loadAssignments);
    loadAssignments();
  });
})();
