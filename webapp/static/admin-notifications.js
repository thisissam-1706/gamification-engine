// Notifications tab (M5): channels, consent, quiet hours, dedup, daily cap, retry, dead letters.
(function () {
  "use strict";

  var CATEGORIES = ["streak_risk", "remediation", "quest_update", "social_nudge", "general"];
  var OUTCOME_KIND = { delivered: "ok", suppressed: "warn", deferred: "info", dead_letter: "bad", queued: "info", failed: "bad" };
  var STATUS_LABEL = { dispatched: "delivered", suppressed: "suppressed", queued: "deferred", failed: "dead letter" };

  function tile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
      '</div><div class="adm-tile-value">' + Admin.esc(value) + "</div></div>";
  }

  Admin.registerTab("notifications", function (root) {
    root.innerHTML =
      '<div id="no-status"></div>' +
      '<div class="adm-grid"><div class="adm-card"><h3>Send</h3>' +
      '<div class="adm-row"><select id="no-channel"><option value="in_app">in_app</option><option value="push">push</option><option value="email">email</option></select>' +
      '<select id="no-category"></select></div>' +
      '<div class="adm-actions"><button class="btn-small" type="button" data-no="one">Send one</button></div>' +
      '<p class="adm-hint">A push to a push-opted-out learner is suppressed by consent (opt_out).</p>' +
      '<div class="adm-actions"><button class="btn-small" type="button" data-no="twice">Send same proposal twice (dedup)</button></div>' +
      '<p class="adm-hint">The second copy is suppressed as duplicate_proposal.</p>' +
      '<div class="adm-actions"><button class="btn-small" type="button" data-no="seven">Send 7 in a row (daily cap)</button></div>' +
      '<p class="adm-hint">The cap is 5 delivered messages in 24 hours across all channels; sends after that are suppressed as daily_cap_exceeded.</p>' +
      '<div id="no-send-out"></div></div>' +
      '<div><div class="adm-card"><h3>Provider health</h3>' +
      '<label class="adm-switch"><input type="checkbox" id="no-out-push"> Push provider outage</label>' +
      '<label class="adm-switch"><input type="checkbox" id="no-out-email"> Email provider outage</label>' +
      '<p class="adm-hint">While an outage is on, sends retry 3 times then land in the dead-letter queue.</p></div>' +
      '<div class="adm-card"><h3>Recovery</h3><div class="adm-actions">' +
      '<button class="btn-small" type="button" data-no="replay">Replay dead letters</button>' +
      '<button class="btn-small" type="button" data-no="release">Release deferred (quiet hours)</button></div>' +
      '<p class="adm-hint">Move the clock to 23:00 with the admin bar, send a push (it is deferred), then move to the morning and release.</p>' +
      '<div id="no-rec-out"></div></div></div></div>' +
      '<div class="adm-card"><h3>Timeline</h3><div id="no-timeline"></div></div>';

    var catSel = root.querySelector("#no-category");
    CATEGORIES.forEach(function (c) {
      var o = document.createElement("option"); o.value = c; o.textContent = c; catSel.appendChild(o);
    });
    var token = 0;
    function base() { return "/api/demo/admin/learners/" + Admin.learnerId() + "/notifications"; }

    function refresh() {
      var t = ++token;
      Admin.api("GET", base()).then(function (d) {
        if (t !== token) return;
        var c = d.counts;
        root.querySelector("#no-status").innerHTML = '<div class="adm-tiles">' +
          tile("Delivered", c.delivered) + tile("Suppressed", c.suppressed) +
          tile("Deferred (quiet hours)", c.deferred) + tile("Dead letters open", c.dead_letter_open) + "</div>" +
          '<div class="adm-chips"><span class="adm-chip ' + (d.opted_out.indexOf("push") >= 0 ? "adm-chip-warn" : "") + '">Push: ' +
          (d.opted_out.indexOf("push") >= 0 ? "opted out" : "on") + "</span>" +
          '<span class="adm-chip adm-chip-muted">Quiet hours ' + pad(d.quiet_hours.start) + ":00-" + pad(d.quiet_hours.end) + ":00</span>" +
          '<span class="adm-chip adm-chip-muted">Clock ' + Admin.esc(d.clock.replace("T", " ").replace("Z", " UTC")) + "</span></div>";
        root.querySelector("#no-out-push").checked = d.outage.push;
        root.querySelector("#no-out-email").checked = d.outage.email;
        root.querySelector("#no-timeline").innerHTML = d.items.length ? Admin.table([
          { label: "Time", render: function (i) { return Admin.esc(Admin.fmtTime(i.time)); } },
          { label: "Channel", key: "channel" },
          { label: "Category", key: "category" },
          { label: "Outcome", render: function (i) {
            var b = Admin.badge(i.outcome.replace("_", " "), OUTCOME_KIND[i.outcome] || "info");
            if (i.outcome === "dead_letter" && i.resolved) b += " " + Admin.badge("resolved", "ok");
            return b;
          } },
          { label: "Details", render: function (i) {
            var parts = [];
            if (i.outcome === "deferred" && i.deliver_at) parts.push("until " + Admin.fmtTime(i.deliver_at, true));
            else if (i.reason) parts.push(i.reason);
            if (i.attempts) parts.push(i.attempts + " attempts");
            return Admin.esc(parts.join(" - "));
          } },
          { label: "Proposal", render: function (i) { return Admin.esc(i.proposal_id); } }
        ], d.items) : '<p class="muted">No notifications for this learner yet.</p>';
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }
    function pad(n) { return String(n).padStart(2, "0"); }

    function showFacts(facts) {
      root.querySelector("#no-send-out").innerHTML = '<div class="adm-strip">' + facts.map(function (f) {
        var label = STATUS_LABEL[f.status] || f.status;
        var kind = f.status === "dispatched" ? "ok" : (f.status === "suppressed" ? "warn" : (f.status === "queued" ? "info" : "bad"));
        return Admin.badge(label, kind) + " " + Admin.esc(f.reason || (f.rescheduled_for ? "until " + Admin.fmtTime(f.rescheduled_for, true) : "")) +
          (f.attempt_count > 1 ? " (" + f.attempt_count + " attempts)" : "");
      }).join(" ") + "</div>";
    }
    function send(extra) {
      var body = { channel: root.querySelector("#no-channel").value, category: catSel.value };
      Object.keys(extra).forEach(function (k) { body[k] = extra[k]; });
      return Admin.api("POST", base() + "/send", body).then(function (d) { showFacts(d.facts); });
    }

    Array.prototype.forEach.call(root.querySelectorAll("button[data-no]"), function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-no"), p;
        if (kind === "one") p = send({});
        else if (kind === "twice") p = send({ repeat_same: true });
        else if (kind === "seven") p = send({ count: 7 });
        else if (kind === "replay") p = Admin.api("POST", base() + "/replay-dlq", {}).then(function (r) {
          root.querySelector("#no-rec-out").innerHTML = '<div class="adm-strip">' + (r.length ? r.map(function (x) {
            return Admin.badge(x.status, x.status === "dispatched" ? "ok" : "bad") + " " + Admin.esc(x.reason || "delivered");
          }).join(" ") : "No open dead letters.") + "</div>";
        });
        else p = Admin.api("POST", base() + "/release-deferred", {}).then(function (r) {
          root.querySelector("#no-rec-out").innerHTML = '<div class="adm-strip">' + (r.length ? r.map(function (x) {
            return Admin.badge(x.status, x.status === "dispatched" ? "ok" : (x.status === "queued" ? "info" : "warn")) + " " + Admin.esc(x.reason || "delivered");
          }).join(" ") : "Nothing due for release at this clock time.") + "</div>";
        });
        btn.disabled = true;
        p.catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () { btn.disabled = false; });
      });
    });
    ["push", "email"].forEach(function (ch) {
      root.querySelector("#no-out-" + ch).addEventListener("change", function (e) {
        Admin.api("POST", base() + "/outage", { channel: ch, active: e.target.checked })
          .catch(function (err) { Admin.toast(err.message, "bad"); });
      });
    });

    Admin.onLearnerChange(function () {
      root.querySelector("#no-send-out").innerHTML = ""; root.querySelector("#no-rec-out").innerHTML = ""; refresh();
    });
    Admin.onChange(refresh);
    refresh();
  });
})();
