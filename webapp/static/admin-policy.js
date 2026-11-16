// Policy tab (M4): remediation decisions, quest assignment, frequency guardrail, expiry sweep.
(function () {
  "use strict";

  var DECISION_TEXT = {
    passed: "the attempt passed, so nothing is needed",
    not_first_attempt: "only the first attempt at an assessment can trigger a quest",
    remediated_within_window: "a quest was already assigned inside the frequency window"
  };

  Admin.registerTab("policy", function (root) {
    root.innerHTML =
      '<div class="adm-card"><h3>Policy settings</h3><div id="po-config"></div></div>' +
      '<div class="adm-card"><h3>Try it</h3>' +
      '<div class="adm-row"><label for="po-assess">Assessment</label><select id="po-assess">' +
      '<option value="fractions_checkpoint_1">fractions_checkpoint_1</option>' +
      '<option value="fractions_checkpoint_2">fractions_checkpoint_2</option></select></div>' +
      '<div class="adm-actions">' +
      '<button class="btn-small" type="button" data-po="fail">Fail checkpoint</button>' +
      '<button class="btn-small" type="button" data-po="pass">Pass checkpoint</button>' +
      '<button class="btn-small" type="button" data-po="complete">Complete remediation lesson</button>' +
      '<button class="btn-small" type="button" data-po="sweep">Run quest-expiry sweep</button></div>' +
      '<p class="adm-hint" id="po-hint"></p><div id="po-result"></div></div>' +
      '<div class="adm-grid"><div class="adm-card"><h3>Checkpoint attempts</h3><div id="po-attempts"></div></div>' +
      '<div class="adm-card"><h3>Engine decisions</h3><div id="po-decisions"></div></div></div>' +
      '<div class="adm-card"><h3>Remediation quests</h3><div id="po-quests"></div></div>';
    var resultEl = root.querySelector("#po-result");
    var token = 0;

    function base() { return "/api/demo/admin/learners/" + Admin.learnerId(); }

    function decisionBadge(d) {
      return Admin.badge(d.decision === "assign" ? "assigned" : (d.reason === "passed" ? "skipped" : "suppressed"),
        d.decision === "assign" ? "ok" : "warn");
    }
    function questName(id) { return id ? Admin.short(id.replace("remediation_quest:", "")) : "-"; }

    function loadConfig() {
      Admin.api("GET", "/api/demo/admin/policy/config").then(function (rows) {
        root.querySelector("#po-config").innerHTML = Admin.table([
          { label: "Setting", key: "name" }, { label: "Value", key: "value" },
          { label: "Meaning", key: "meaning" }
        ], rows);
        var win = "14 days", dl = "7 days";
        rows.forEach(function (r) {
          if (r.name === "frequency_window") win = r.value;
          if (r.name === "quest_deadline") dl = r.value;
        });
        root.querySelector("#po-hint").textContent =
          "Guardrail: the first failed attempt at an assessment assigns one remediation quest (deadline " + dl + "). " +
          "Fail the same assessment again and the second is recorded but no quest is assigned (not_first_attempt). " +
          "Fail a different assessment within " + win + " and it is also held back (remediated_within_window). " +
          "Pass a checkpoint and the decision is no_action (passed).";
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    function refresh() {
      var t = ++token;
      Admin.api("GET", base() + "/policy").then(function (p) {
        if (t !== token) return;
        root.querySelector("#po-attempts").innerHTML = p.attempts.length ? Admin.table([
          { label: "Time", render: function (a) { return Admin.esc(Admin.fmtTime(a.occurred_at)); } },
          { label: "Attempt", render: function (a) { return Admin.esc(Admin.short(a.attempt_id.replace("att_", ""))) + " " + Admin.esc(a.assessment_id); } },
          { label: "Result", render: function (a) { return a.passed ? Admin.badge("pass", "ok") : Admin.badge("fail", "bad"); } }
        ], p.attempts) : '<p class="muted">No checkpoint attempts yet.</p>';
        root.querySelector("#po-decisions").innerHTML = p.decisions.length ? Admin.table([
          { label: "Time", render: function (d) { return Admin.esc(Admin.fmtTime(d.decided_at)); } },
          { label: "Attempt", render: function (d) { return Admin.esc(Admin.short(d.attempt_id.replace("att_", ""))); } },
          { label: "Decision", render: decisionBadge },
          { label: "Reason", render: function (d) { return Admin.esc(d.reason || "-"); } },
          { label: "Quest", render: function (d) { return Admin.esc(questName(d.assignment_id)); } }
        ], p.decisions) : '<p class="muted">The engine has not decided anything for this learner yet.</p>';
        root.querySelector("#po-quests").innerHTML = p.quests.length ? Admin.table([
          { label: "Quest", render: function (q) { return Admin.esc(q.quest_id + " " + questName(q.assignment_id)); } },
          { label: "Status", render: function (q) {
            return Admin.badge(q.status, q.status === "completed" ? "ok" : (q.status === "expired" ? "bad" : "info"));
          } },
          { label: "Assigned", render: function (q) { return Admin.esc(Admin.fmtTime(q.assigned_at)); } },
          { label: "Deadline", render: function (q) { return Admin.esc(Admin.fmtTime(q.deadline_at)); } }
        ], p.quests) : '<p class="muted">No remediation quests for this learner.</p>';
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    function checkpoint(passed) {
      var assess = root.querySelector("#po-assess").value;
      return Admin.api("POST", base() + "/policy/checkpoint", { passed: passed, assessment_id: assess }).then(function (d) {
        var dec = d.latest_decision;
        var html = '<div class="adm-strip">' + Admin.badge(d.result.status, d.result.status === "accepted" ? "ok" : "warn") +
          "<span>" + Admin.esc(d.result.reason || "") + "</span>";
        if (dec) {
          html += "<span>Engine decision: " + decisionBadge(dec) + "</span><span>" +
            Admin.esc(dec.reason ? dec.reason + " - " + (DECISION_TEXT[dec.reason] || "") : "a remediation quest was assigned") + "</span>";
        }
        resultEl.innerHTML = html + "</div>";
      });
    }

    Array.prototype.forEach.call(root.querySelectorAll("button[data-po]"), function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-po");
        var promise;
        if (kind === "fail") promise = checkpoint(false);
        else if (kind === "pass") promise = checkpoint(true);
        else if (kind === "complete") {
          promise = Admin.api("POST", base() + "/policy/complete-remediation", {}).then(function (d) {
            resultEl.innerHTML = '<div class="adm-strip">' + Admin.badge(d.result.status, d.result.status === "accepted" ? "ok" : "warn") +
              "<span>Remediation lesson. XP now " + d.snapshot.xp_total + "</span></div>";
          });
        } else {
          promise = Admin.api("POST", "/api/demo/admin/policy/expiry-sweep", { learner_id: Admin.learnerId() }).then(function (d) {
            resultEl.innerHTML = '<div class="adm-strip"><span>Swept ' + d.learners_swept + " learners at " +
              Admin.esc(Admin.fmtTime(d.now, true)) + ": " + d.expired_count + " quest(s) expired" +
              (d.expired_quest_ids.length ? " (" + Admin.esc(d.expired_quest_ids.join(", ")) + ")" : "") + "</span></div>";
          });
        }
        btn.disabled = true;
        promise.catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () { btn.disabled = false; });
      });
    });

    Admin.onLearnerChange(function () { resultEl.innerHTML = ""; refresh(); });
    Admin.onChange(refresh);
    loadConfig();
    refresh();
  });
})();
