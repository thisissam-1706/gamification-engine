// Quests & Policy tab (M4): the selected learner's quests and the remediation policy's settings.
(function () {
  "use strict";

  var STATUS_KIND = { assigned: "info", completed: "ok", expired: "bad", overdue: "warn" };

  Admin.registerTab("policy", function (root) {
    root.innerHTML =
      '<div class="adm-card"><h3>Quests for this learner</h3><div id="po-quests"></div></div>' +
      '<details class="adm-card adm-details"><summary>Policy settings (the numbers the rule uses)</summary><div id="po-config"></div></details>';
    var token = 0;

    function base() { return "/api/demo/admin/learners/" + Admin.learnerId(); }

    function loadConfig() {
      Admin.api("GET", "/api/demo/admin/policy/config").then(function (rows) {
        root.querySelector("#po-config").innerHTML = Admin.table([
          { label: "Setting", key: "name" }, { label: "Value", key: "value" },
          { label: "Meaning", key: "meaning" }
        ], rows);
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    function progressBar(q) {
      var pct = q.target ? Math.round(100 * q.progress / q.target) : 0;
      return '<div class="adm-meter-bar" title="' + q.progress + " of " + q.target + '"><span style="width:' + pct + '%"></span></div>';
    }

    function refresh() {
      var t = ++token;
      Admin.api("GET", base() + "/quests").then(function (q) {
        if (t !== token) return;
        root.querySelector("#po-quests").innerHTML = q.quests.length ? Admin.table([
          { label: "Quest", render: function (x) {
            return "<strong>" + Admin.esc(x.title) + '</strong><div class="adm-hint">' + Admin.esc(x.how) + "</div>";
          } },
          { label: "Progress", render: function (x) {
            return Admin.esc(x.progress + " / " + x.target) + progressBar(x) + '<div class="adm-hint">counts ' + Admin.esc(x.counts) + "</div>";
          } },
          { label: "Status", render: function (x) {
            var label = x.status === "overdue" ? "overdue (sweep not run)" : x.status;
            return Admin.badge(label, STATUS_KIND[x.status] || "info");
          } },
          { label: "Assigned", render: function (x) { return Admin.esc(Admin.fmtTime(x.assigned_at)); } },
          { label: "Deadline", render: function (x) { return Admin.esc(Admin.fmtTime(x.deadline_at)); } }
        ], q.quests) : '<p class="muted">No quests yet.</p>';
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    Admin.onLearnerChange(refresh);
    Admin.onChange(refresh);
    loadConfig();
    refresh();
  });
})();
