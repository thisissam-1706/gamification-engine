// Notifications tab (M5): where each channel goes, consent, the daily cap, the delivered-messages
// outbox and the timeline. Provider outages are toggled from the Demo split view's menu.
(function () {
  "use strict";

  var CATEGORIES = ["streak_risk", "remediation", "quest_update", "social_nudge", "general"];
  var OUTCOME_KIND = { delivered: "ok", suppressed: "warn", deferred: "info", dead_letter: "bad", queued: "info", failed: "bad" };
  var STATUS_LABEL = { dispatched: "delivered", suppressed: "suppressed", queued: "deferred", failed: "dead letter" };
  var REASON_TEXT = {
    opt_out: "learner turned this channel off",
    duplicate_proposal: "same proposal id already handled",
    daily_cap_exceeded: "5 messages already delivered today on this channel",
    no_active_quest: "no quest in progress, so no quest update",
    quiet_hours_rescheduled: "quiet hours: held until 08:00"
  };

  function tile(label, value) {
    return '<div class="adm-tile"><div class="adm-tile-label">' + Admin.esc(label) +
      '</div><div class="adm-tile-value">' + Admin.esc(value) + "</div></div>";
  }
  function hoursUntil(iso, clockIso) {
    var h = (new Date(iso) - new Date(clockIso)) / 3600000;
    return h < 1 ? Math.max(1, Math.round(h * 60)) + " min" : Math.round(h) + " h";
  }

  Admin.registerTab("notifications", function (root) {
    root.innerHTML =
      '<div id="no-status"></div>' +

      '<div class="adm-card adm-explainer"><h3>Where each channel goes</h3><div class="adm-grid3" id="no-where"></div>' +
      '<div id="no-email-account"></div></div>' +

      '<div class="adm-grid"><div class="adm-card"><h3>Send</h3>' +
      '<div class="adm-row"><select id="no-channel"><option value="in_app">in_app</option><option value="push">push</option><option value="email">email</option></select>' +
      '<select id="no-category"></select></div>' +
      '<div class="adm-actions"><button class="btn-small" type="button" data-no="one">Send one</button></div>' +
      '<p class="adm-hint">A push to a push-opted-out learner is suppressed by consent (opt_out). A quest_update is only sent while the learner has a quest in progress.</p>' +
      '<div class="adm-actions"><button class="btn-small" type="button" data-no="seven">Send 7 in a row (daily cap)</button></div>' +
      '<p class="adm-hint">At most 5 messages per channel are delivered per learner per day: 5 in-app, 5 push and 5 email, each counted on its own. The count resets at midnight in the learner\'s timezone ("Next day 09:00" starts a fresh day).</p>' +
      '<div id="no-send-out"></div></div>' +

      '<div class="adm-card"><h3>Delivered to phone and email</h3><div id="no-outbox"></div></div></div>' +

      '<div class="adm-card"><h3>Timeline (every decision for this learner)</h3><div id="no-timeline"></div></div>';

    var catSel = root.querySelector("#no-category");
    CATEGORIES.forEach(function (c) {
      var o = document.createElement("option"); o.value = c; o.textContent = c; catSel.appendChild(o);
    });
    var token = 0;
    var lastClock = null;
    function base() { return "/api/demo/admin/learners/" + Admin.learnerId() + "/notifications"; }

    function renderWhere(d) {
      var w = d.where;
      root.querySelector("#no-where").innerHTML =
        '<div><strong>In-app</strong><p class="adm-hint">' + Admin.esc(w.in_app) + "</p></div>" +
        '<div><strong>Push</strong><p class="adm-hint">' + Admin.esc(w.push) + "</p></div>" +
        '<div><strong>Email</strong><p class="adm-hint">' + Admin.esc(w.email) + " This learner's address: <code>" +
        Admin.esc(d.email_address) + "</code></p></div>";
      var a = w.email_account, html;
      if (a) {
        html = '<div class="adm-strip">' + Admin.badge("email: " + (a.provider || "smtp"), "ok") +
          "<span>" + Admin.esc(a.host + ":" + a.port) + "</span>" + (a.user ? "<span>login <code>" + Admin.esc(a.user) + "</code></span>" : "") +
          (a.login_password ? "<span>password <code>" + Admin.esc(a.login_password) + "</code></span>" : "") +
          (a.web ? '<a href="' + Admin.esc(a.provider === "ethereal" ? a.web.replace(/\/$/, "") + "/login" : a.web) + '" target="_blank" rel="noopener">Open the inbox</a>' : "") +
          (a.source === "file" ? '<button class="btn-small" type="button" id="no-email-clear">Disconnect</button>' : "<span class=\"muted\">(set by environment)</span>") +
          "</div><p class=\"adm-hint\">Send an email below, then open the inbox" + (a.provider === "ethereal" ? " (log in with the login and password above, then Messages)" : "") + ". Every email to any learner address lands there.</p>";
      } else {
        html = '<div class="adm-actions"><button class="btn-small" type="button" id="no-email-mailpit">Use local Mailpit inbox</button>' +
          '<button class="btn-small" type="button" id="no-email-ethereal">Create a free Ethereal fake inbox</button></div>' +
          '<details class="adm-details"><summary>Use your own SMTP test inbox instead (Mailtrap, MailHog, ...)</summary>' +
          '<div class="adm-row"><input id="no-smtp-host" placeholder="host"><input id="no-smtp-port" placeholder="port" value="587" size="5">' +
          '<input id="no-smtp-user" placeholder="user"><input id="no-smtp-pass" placeholder="password" type="password">' +
          '<input id="no-smtp-web" placeholder="inbox web URL (optional)"><button class="btn-small" type="button" id="no-smtp-save">Save</button></div></details>';
      }
      root.querySelector("#no-email-account").innerHTML = html;
      var create = root.querySelector("#no-email-ethereal");
      if (create) create.addEventListener("click", function () {
        create.disabled = true; create.textContent = "Creating...";
        Admin.api("POST", "/api/demo/admin/email/ethereal", {}).then(function () {
          Admin.toast("Ethereal inbox created", "ok");
        }).catch(function (e) { Admin.toast(e.message, "bad"); create.disabled = false; create.textContent = "Create a free Ethereal fake inbox"; });
      });
      var mailpit = root.querySelector("#no-email-mailpit");
      if (mailpit) mailpit.addEventListener("click", function () {
        Admin.api("POST", "/api/demo/admin/email/mailpit", {}).then(function () {
          Admin.toast("Email now goes to Mailpit (localhost:1025)", "ok");
        }).catch(function (e) { Admin.toast(e.message, "bad"); });
      });
      var clear = root.querySelector("#no-email-clear");
      if (clear) clear.addEventListener("click", function () {
        Admin.api("POST", "/api/demo/admin/email/account/clear", {}).catch(function (e) { Admin.toast(e.message, "bad"); });
      });
      var save = root.querySelector("#no-smtp-save");
      if (save) save.addEventListener("click", function () {
        function v(id) { return root.querySelector(id).value.trim(); }
        Admin.api("POST", "/api/demo/admin/email/account", {
          host: v("#no-smtp-host"), port: Number(v("#no-smtp-port") || 587), user: v("#no-smtp-user"),
          password: v("#no-smtp-pass"), web: v("#no-smtp-web") || null, provider: "smtp"
        }).then(function () { Admin.toast("SMTP account saved", "ok"); }).catch(function (e) { Admin.toast(e.message, "bad"); });
      });
    }

    function renderOutbox() {
      return Admin.api("GET", base() + "/outbox").then(function (d) {
        var el = root.querySelector("#no-outbox");
        if (!d.messages.length) {
          el.innerHTML = '<p class="muted">Nothing delivered to the phone or by email yet. Send a push or an email.</p>';
          return;
        }
        el.innerHTML = '<div class="adm-outbox">' + d.messages.map(function (m) {
          var push = m.channel === "push";
          return '<div class="adm-msg ' + (push ? "is-push" : "is-email") + '">' +
            '<div class="adm-msg-top"><span>' + (push ? "Phone notification" : "Email to " + Admin.esc(m.recipient)) + "</span><span>" +
            Admin.esc(Admin.fmtTime(m.sent_at)) + "</span></div>" +
            "<strong>" + Admin.esc(m.title) + "</strong><div>" + Admin.esc(m.body) + "</div>" +
            '<div class="adm-hint">via ' + Admin.esc(m.provider) + " - " + Admin.esc(m.category) +
            (m.preview_url ? ' - <a href="' + Admin.esc(m.preview_url) + '" target="_blank" rel="noopener">open this email</a>' : "") +
            "</div></div>";
        }).join("") + "</div>";
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
    }

    function refresh() {
      var t = ++token;
      Admin.api("GET", base()).then(function (d) {
        if (t !== token) return;
        lastClock = d.clock;
        var c = d.counts, cap = d.cap;
        root.querySelector("#no-status").innerHTML = '<div class="adm-tiles">' +
          tile("Delivered", c.delivered) + tile("Suppressed", c.suppressed) +
          tile("Held for quiet hours", c.deferred) + tile("Dead letters open", c.dead_letter_open) +
          tile("Daily cap used today (" + cap.limit + " per channel)", "in-app " + cap.per_channel.in_app + " · push " + cap.per_channel.push + " · email " + cap.per_channel.email) + "</div>" +
          '<div class="adm-chips"><span class="adm-chip ' + (d.opted_out.indexOf("push") >= 0 ? "adm-chip-warn" : "") + '">Push: ' +
          (d.opted_out.indexOf("push") >= 0 ? "opted out" : "on") + "</span>" +
          '<span class="adm-chip ' + (cap.used >= cap.limit ? "adm-chip-warn" : "adm-chip-muted") + '">Cap resets at 00:00 ' + Admin.esc(cap.timezone) +
          " (in " + hoursUntil(cap.resets_at, d.clock) + ")</span>" +
          '<span class="adm-chip adm-chip-muted">Quiet hours ' + pad(d.quiet_hours.start) + ":00-" + pad(d.quiet_hours.end) + ":00</span>" +
          '<span class="adm-chip adm-chip-muted">Clock ' + Admin.esc(d.clock.replace("T", " ").replace("Z", " UTC")) + "</span></div>";
        renderWhere(d);
        root.querySelector("#no-timeline").innerHTML = d.items.length ? Admin.table([
          { label: "Time", render: function (i) { return Admin.esc(Admin.fmtTime(i.time)); } },
          { label: "Channel", key: "channel" },
          { label: "Category", key: "category" },
          { label: "Outcome", render: function (i) {
            var b = Admin.badge(i.outcome === "deferred" ? "held" : i.outcome.replace("_", " "), OUTCOME_KIND[i.outcome] || "info");
            if (i.outcome === "dead_letter" && i.resolved) b += " " + Admin.badge("resolved", "ok");
            return b;
          } },
          { label: "Details", render: function (i) {
            var parts = [];
            if (i.outcome === "deferred" && i.deliver_at) parts.push("until " + Admin.fmtTime(i.deliver_at, true) + (/released/.test(i.reason || "") ? " (released)" : ""));
            else if (i.reason) parts.push(REASON_TEXT[i.reason] ? i.reason + ": " + REASON_TEXT[i.reason] : i.reason);
            if (i.attempts) parts.push(i.attempts + " attempts");
            return Admin.esc(parts.join(" - "));
          } },
          { label: "Proposal", render: function (i) { return Admin.esc(i.proposal_id); } }
        ], d.items) : '<p class="muted">No notifications for this learner yet.</p>';
      }).catch(function (e) { Admin.toast(e.message, "bad"); });
      renderOutbox();
    }
    function pad(n) { return String(n).padStart(2, "0"); }

    function factsHtml(facts) {
      return '<div class="adm-strip">' + facts.map(function (f) {
        var label = STATUS_LABEL[f.status] || f.status;
        var kind = f.status === "dispatched" ? "ok" : (f.status === "suppressed" ? "warn" : (f.status === "queued" ? "info" : "bad"));
        return Admin.badge(label, kind) + " " + Admin.esc(f.reason || (f.rescheduled_for ? "until " + Admin.fmtTime(f.rescheduled_for, true) : "")) +
          (f.attempt_count > 1 ? " (" + f.attempt_count + " attempts)" : "");
      }).join(" ") + "</div>";
    }
    function send(extra, outId) {
      var body = { channel: root.querySelector("#no-channel").value, category: catSel.value };
      Object.keys(extra).forEach(function (k) { body[k] = extra[k]; });
      return Admin.api("POST", base() + "/send", body).then(function (d) {
        root.querySelector(outId || "#no-send-out").innerHTML = factsHtml(d.facts);
      });
    }

    Array.prototype.forEach.call(root.querySelectorAll("button[data-no]"), function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-no"), p;
        if (kind === "one") p = send({});
        else p = send({ count: 7 });
        btn.disabled = true;
        p.catch(function (e) { Admin.toast(e.message, "bad"); }).then(function () { btn.disabled = false; });
      });
    });
    Admin.onLearnerChange(function () {
      root.querySelector("#no-send-out").innerHTML = ""; refresh();
    });
    Admin.onChange(refresh);
    refresh();
  });
})();
