// Cohorts tab (M3): membership, ranks, consistency semantics, measured latency.
(function () {
  "use strict";

  function tile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
      '</div><div class="adm-tile-value">' + Admin.esc(value) + "</div></div>";
  }
  function arrow(a, b) {
    return (a === null || a === undefined ? "-" : a) + " → " + (b === null || b === undefined ? "-" : b);
  }
  // Cohort ids look like cohort_act_<epoch>_<tier>_<n>__cohort_mod_<epoch>_<module>_<n>.
  function describe(cohortId) {
    var m = /_(casual|regular|intensive)_\d+__cohort_mod_[^_]+_(.+)_\d+$/.exec(cohortId || "");
    return m ? "activity tier " + m[1] + ", module " + m[2] : cohortId;
  }

  Admin.registerTab("cohorts", function (root) {
    root.innerHTML =
      '<div class="adm-card" id="co-cohorts"><p class="muted">Loading...</p></div>' +
      '<div class="adm-card" id="co-consistency"></div>' +
      '<div class="adm-card"><h3>Latency</h3>' +
      '<div class="adm-actions"><button class="btn-small" type="button" id="co-probe">Run query probe</button>' +
      '<button class="btn-small" type="button" id="co-measure">Send a lesson and measure update</button></div>' +
      '<div id="co-probe-out"></div><div id="co-measure-out"></div></div>';
    var cohortsEl = root.querySelector("#co-cohorts");
    var consEl = root.querySelector("#co-consistency");
    var probeOut = root.querySelector("#co-probe-out");
    var measureOut = root.querySelector("#co-measure-out");
    var token = 0;

    function renderCohorts(data) {
      var html = "<h3>Cohorts (week " + Admin.esc(data.epoch) + ")</h3>";
      data.cohorts.forEach(function (c) {
        html += '<div class="adm-card"><strong>' + Admin.esc(c.name) + "</strong> " +
          '<span class="muted">' + c.member_count + " members</span>";
        var table = Admin.table([
          { label: "Rank", render: function (m) { return Admin.esc(m.cohort_rank === null ? "-" : m.cohort_rank); } },
          { label: "Learner", key: "display_name" },
          { label: "XP", render: function (m) { return Admin.esc(m.cohort_score === null ? "-" : m.cohort_score); } },
          { label: "Global rank", render: function (m) { return Admin.esc(m.global_rank === null ? "-" : m.global_rank); } },
          { label: "First reached", render: function (m) { return Admin.esc(Admin.fmtTime(m.first_reached_at)); } }
        ], c.members);
        // highlight the selected learner's row
        var rows = table.split("<tr>");
        var me = Admin.learnerId();
        var out = rows[0] + "<tr>" + rows[1]; // header
        for (var i = 2; i < rows.length; i++) {
          var member = c.members[i - 2];
          out += (member && member.learner_id === me ? '<tr class="adm-row-me">' : "<tr>") + rows[i];
        }
        html += out;
        var first = c.members[0];
        var cons = first && first.constraints ? first.constraints : {};
        html += '<p class="adm-hint">Grouped by ' + Admin.esc(describe(c.cohort_id)) +
          (cons.grouping_rule_version ? " (rule " + Admin.esc(cons.grouping_rule_version) + ")" : "") +
          (cons.assigned_at ? ", assigned " + Admin.esc(Admin.fmtTime(cons.assigned_at)) : "") + ".</p></div>";
      });
      cohortsEl.innerHTML = html;
    }

    function renderConsistency(c) {
      consEl.innerHTML = "<h3>Consistency</h3>" +
        '<div class="adm-tiles">' + tile("Projection offset", c.projection_offset) +
        tile("Log head", c.log_head) + tile("Lag", c.lag) + tile("Mode", c.mode) + "</div>" +
        '<ul class="adm-bullets">' + c.semantics.map(function (s) { return "<li>" + Admin.esc(s) + "</li>"; }).join("") + "</ul>";
    }

    function refresh() {
      var t = ++token;
      Promise.all([
        Admin.api("GET", "/api/demo/admin/cohorts"),
        Admin.api("GET", "/api/demo/admin/cohorts/consistency")
      ]).then(function (res) {
        if (t !== token) return;
        renderCohorts(res[0]);
        renderConsistency(res[1]);
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    root.querySelector("#co-probe").addEventListener("click", function () {
      var btn = this; btn.disabled = true;
      Admin.api("POST", "/api/demo/admin/cohorts/latency-probe", {}).then(function (d) {
        function row(label, o) {
          return "<tr><td>" + label + "</td><td>" + o.p50 + "</td><td>" + o.p95 + "</td><td>" + o.p99 + "</td><td>" + o.max + "</td></tr>";
        }
        probeOut.innerHTML = '<div class="adm-table-wrap"><table class="adm-table"><thead><tr><th>' + d.queries +
          " queries (ms)</th><th>p50</th><th>p95</th><th>p99</th><th>max</th></tr></thead><tbody>" +
          row("Rank lookup", d.rank_ms) + row("Top-N lookup", d.top_ms) + "</tbody></table></div>";
      }).catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () { btn.disabled = false; });
    });

    root.querySelector("#co-measure").addEventListener("click", function () {
      var btn = this; btn.disabled = true;
      Admin.api("POST", "/api/demo/admin/learners/" + Admin.learnerId() + "/cohorts/measure-update", {}).then(function (d) {
        var t = d.timings_ms;
        var html = '<div class="adm-strip">';
        if (t) {
          html += "<span>Ingest " + t.ingest + " ms + projection " + t.project + " ms = " +
            (Math.round((t.ingest + t.project) * 100) / 100) + " ms</span>";
        } else {
          html += "<span>" + Admin.esc(d.result.status + (d.result.reason ? ": " + d.result.reason : "")) + "</span>";
        }
        html += "<span>Cohort rank " + arrow(d.before.cohort_rank, d.after.cohort_rank) + "</span>" +
          "<span>Global rank " + arrow(d.before.global_rank, d.after.global_rank) + "</span>" +
          "<span>XP +" + (d.after.xp_total - d.before.xp_total) + "</span></div>";
        measureOut.innerHTML = html;
      }).catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () { btn.disabled = false; });
    });

    Admin.onLearnerChange(function () { measureOut.innerHTML = ""; refresh(); });
    Admin.onChange(refresh);
    refresh();
  });
})();
