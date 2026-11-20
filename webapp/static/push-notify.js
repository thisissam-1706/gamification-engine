// Simulated phone push, shown as a real browser notification.
//
// The demo has no push provider (Firebase/APNs), so a "delivered" push is written to the demo
// outbox. This script watches the selected learner's outbox and, for each new push, shows a
// system notification (Notification API, after the viewer allows it) plus an in-page banner at
// the top of the page as a fallback. Only top-level pages run it, so the split view's iframes
// do not show each message twice.
(function () {
  "use strict";

  var POLL_MS = 2500;
  var seen = {};          // learnerId -> highest outbox seq already shown
  var timer = null;
  var getLearner = null;
  var enableButton = null;

  function supported() { return "Notification" in window; }

  function paintButton() {
    if (!enableButton) return;
    if (!supported()) {
      enableButton.textContent = "Browser notifications not supported";
      enableButton.disabled = true;
      return;
    }
    var p = Notification.permission;
    enableButton.textContent = p === "granted" ? "Phone push: browser notifications on" :
      p === "denied" ? "Notifications blocked (banner only)" : "Show pushes as browser notifications";
    enableButton.disabled = p !== "default";
  }

  function stack() {
    var el = document.getElementById("push-stack");
    if (!el) {
      el = document.createElement("div");
      el.id = "push-stack";
      el.className = "push-stack";
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    return el;
  }

  function banner(msg) {
    var card = document.createElement("div");
    card.className = "push-banner";
    card.setAttribute("role", "status");
    var head = document.createElement("div");
    head.className = "push-banner-head";
    head.textContent = "Streakwise · push";
    var title = document.createElement("strong");
    title.textContent = msg.title || "Streakwise";
    var body = document.createElement("div");
    body.textContent = msg.body || "";
    card.appendChild(head);
    card.appendChild(title);
    card.appendChild(body);
    card.addEventListener("click", function () { card.remove(); });
    stack().prepend(card);
    window.setTimeout(function () { card.classList.add("is-leaving"); }, 7000);
    window.setTimeout(function () { card.remove(); }, 7600);
  }

  function system(msg) {
    if (!supported() || Notification.permission !== "granted") return;
    try {
      var n = new Notification(msg.title || "Streakwise", {
        body: msg.body || "", tag: msg.proposal_id || undefined, icon: undefined
      });
      n.onclick = function () { window.focus(); n.close(); };
    } catch (e) { /* some browsers only allow notifications from a service worker */ }
  }

  function poll() {
    var id = getLearner && getLearner();
    if (!id) return;
    fetch("/api/demo/admin/learners/" + encodeURIComponent(id) + "/notifications/outbox")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.messages) return;
        var pushes = data.messages.filter(function (m) { return m.channel === "push"; });
        var top = pushes.reduce(function (max, m) { return Math.max(max, m.seq); }, 0);
        if (!(id in seen)) {  // first look at this learner: older pushes are not news
          seen[id] = top;
          return;
        }
        pushes.filter(function (m) { return m.seq > seen[id]; })
          .sort(function (a, b) { return a.seq - b.seq; })
          .forEach(function (m) { banner(m); system(m); });
        seen[id] = Math.max(seen[id], top);
      })
      .catch(function () { /* server restarting or demo mode off: try again next tick */ });
  }

  window.StreakwisePush = {
    // getLearnerId: function returning the selected learner id; button: optional enable button.
    start: function (getLearnerId, button) {
      if (window.top !== window) return;  // embedded frame: the parent page shows pushes
      getLearner = getLearnerId;
      enableButton = button || null;
      if (enableButton) {
        enableButton.addEventListener("click", function () {
          if (!supported()) return;
          Notification.requestPermission().then(paintButton);
        });
      }
      paintButton();
      if (timer) window.clearInterval(timer);
      poll();
      timer = window.setInterval(poll, POLL_MS);
    },
    // Forget what was seen (e.g. after Reset demo, when outbox sequence numbers restart).
    reset: function () { seen = {}; }
  };
})();
