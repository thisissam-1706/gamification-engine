(function () {
  "use strict";

  var PRIYA_ID = "a4b67f76-95be-50c1-8157-55022bf4e70d";
  var state = { learnerId: null, requestInFlight: false, clockBase: null, clockFetchedAt: 0, frozen: false };

  function $(id) { return document.getElementById(id); }
  function requestJSON(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          var error = new Error(body.message || body.detail || ("Request failed (" + response.status + ")"));
          error.status = response.status;
          throw error;
        }
        return body;
      });
    });
  }
  function setError(error) {
    $("demo-error-text").textContent = error.message || "The demo request failed.";
    $("demo-error").hidden = false;
  }
  function formatClock(value) {
    var date = new Date(value);
    return isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("en-GB", {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
      second: "2-digit", timeZone: "UTC", hour12: false
    }).format(date) + " UTC";
  }
  function updateURL() {
    var url = new URL(window.location.href);
    url.searchParams.set("learner", state.learnerId);
    window.history.replaceState({}, "", url);
    var link = $("admin-link");
    if (link) link.href = "admin.html?learner=" + encodeURIComponent(state.learnerId);
  }
  function frameURL(page) {
    return page + "?embed=1&learner=" + encodeURIComponent(state.learnerId);
  }
  function reloadFrames() {
    $("lesson-frame").src = frameURL("lesson.html");
    $("home-frame").src = frameURL("index.html");
  }
  function currentClockDate() {
    if (state.clockBase === null) return null;
    return new Date(state.clockBase.getTime() + (state.frozen ? 0 : Date.now() - state.clockFetchedAt));
  }
  function paintClock() {
    var now = currentClockDate();
    if (!now) return;
    $("demo-clock").textContent = formatClock(now.toISOString()) + (state.frozen ? " · frozen" : " · live");
    $("freeze-clock").textContent = state.frozen ? "Resume clock" : "Freeze clock";
  }
  function refreshClock() {
    return requestJSON("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock")
      .then(function (clock) {
        state.clockBase = new Date(clock.now_utc);
        state.clockFetchedAt = Date.now();
        state.frozen = !!clock.frozen;
        paintClock();
        return new Date(clock.now_utc);
      });
  }
  function toggleFreeze() {
    if (state.requestInFlight) return;
    state.requestInFlight = true;
    return requestJSON("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock/freeze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frozen: !state.frozen })
    }).then(refreshClock).catch(setError).finally(function () { state.requestInFlight = false; });
  }
  function moveClock(adjust) {
    if (state.requestInFlight) return;
    state.requestInFlight = true;
    Array.prototype.forEach.call(document.querySelectorAll(".demo-controls button, .demo-controls select"), function (control) {
      control.disabled = true;
    });
    return refreshClock().then(function (clock) {
      adjust(clock);
      return requestJSON("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ now_utc: clock.toISOString() })
      });
    }).then(refreshClock).then(function () {
      $("lesson-frame").src = frameURL("lesson.html");
    }).catch(setError).finally(function () {
      state.requestInFlight = false;
      Array.prototype.forEach.call(document.querySelectorAll(".demo-controls button, .demo-controls select"), function (control) {
        control.disabled = false;
      });
    });
  }
  function resetDemo() {
    if (state.requestInFlight) return;
    state.requestInFlight = true;
    return requestJSON("/api/demo/reset", { method: "POST" }).then(function () {
      try {
        Object.keys(window.localStorage).forEach(function (key) {
          if (key.indexOf("demoLessonIndex:") === 0) window.localStorage.removeItem(key);
        });
      } catch (e) {}
      reloadFrames();
      return refreshClock();
    }).catch(setError).finally(function () { state.requestInFlight = false; });
  }
  // Push provider health, from the learner side: take it down, let the scheduler's reminders
  // fail into the dead-letter queue, bring it back and watch the recovery worker deliver them.
  function paintProvider(outage) {
    var down = !!(outage && outage.push);
    $("push-provider").textContent = "Push provider: " + (down ? "DOWN" : "up");
    $("push-provider").classList.toggle("is-down", down);
    $("push-provider-note").textContent = down ?
      "Pushes now fail, retry 3 times and wait in the dead-letter queue." : "";
    showBadge(down ? "Push provider DOWN" : "", false);
  }
  var badgeTimer = null;
  function showBadge(text, ok) {
    var badge = $("push-badge");
    clearTimeout(badgeTimer);
    badge.textContent = text;
    badge.hidden = !text;
    badge.classList.toggle("is-ok", !!ok);
    if (text && ok) badgeTimer = setTimeout(function () { badge.hidden = true; }, 8000);
  }
  // The "more" menu: opens on the dots, closes on an outside click, Escape, or after an action.
  function setMenu(open) {
    $("more-menu").hidden = !open;
    $("more-toggle").setAttribute("aria-expanded", open ? "true" : "false");
  }
  function wireMenu() {
    $("more-toggle").addEventListener("click", function (event) {
      event.stopPropagation();
      setMenu($("more-menu").hidden);
    });
    $("push-badge").addEventListener("click", function (event) { event.stopPropagation(); setMenu(true); });
    $("more-menu").addEventListener("click", function (event) {
      event.stopPropagation();
      // Keep it open after toggling the provider so the explanation under it stays visible.
      if (event.target.closest(".demo-menu-item") && event.target.id !== "push-provider") setMenu(false);
    });
    document.addEventListener("click", function () { setMenu(false); });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !$("more-menu").hidden) { setMenu(false); $("more-toggle").focus(); }
    });
  }
  function refreshProvider() {
    return requestJSON("/api/demo/admin/learners/" + encodeURIComponent(state.learnerId) + "/notifications")
      .then(function (d) { paintProvider(d.outage); });
  }
  function toggleProvider() {
    var down = $("push-provider").classList.contains("is-down");
    return requestJSON("/api/demo/admin/learners/" + encodeURIComponent(state.learnerId) + "/notifications/outage", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: "push", active: !down })
    }).then(function (d) {
      paintProvider(d);
      if (down && d.recovered) {
        $("push-provider-note").textContent = "Provider back: the recovery worker re-sent " + d.recovered +
          " dead-lettered push" + (d.recovered === 1 ? "" : "es") + ".";
        showBadge("Push back: re-sent " + d.recovered, true);
      }
    }).catch(setError);
  }
  function init() {
    return requestJSON("/api/demo/personas").then(function (personas) {
      var learners = personas.map(function (p) {
        return { id: p.learner_id, display_name: p.display_name + " - " + p.role };
      });
      var requested = new URLSearchParams(window.location.search).get("learner");
      state.learnerId = learners.some(function (learner) { return learner.id === requested; }) ? requested :
        (learners.some(function (learner) { return learner.id === PRIYA_ID; }) ? PRIYA_ID : learners[0].id);
      var select = $("demo-learner");
      learners.forEach(function (learner) {
        var option = document.createElement("option");
        option.value = learner.id;
        option.textContent = learner.display_name;
        option.selected = learner.id === state.learnerId;
        select.appendChild(option);
      });
      updateURL();
      reloadFrames();
      window.StreakwisePush.start(function () { return state.learnerId; }, $("push-permission"));
      refreshProvider().catch(function () {});
      return refreshClock();
    }).catch(setError);
  }

  $("demo-learner").addEventListener("change", function (event) {
    state.learnerId = event.target.value;
    updateURL();
    reloadFrames();
    refreshClock().catch(setError);
  });
  $("plus-1h").addEventListener("click", function () {
    moveClock(function (clock) { clock.setTime(clock.getTime() + 60 * 60 * 1000); });
  });
  $("plus-6h").addEventListener("click", function () {
    moveClock(function (clock) { clock.setTime(clock.getTime() + 6 * 60 * 60 * 1000); });
  });
  $("same-day").addEventListener("click", function () {
    moveClock(function (clock) { clock.setUTCHours(20, 0, 0, 0); });
  });
  $("next-day").addEventListener("click", function () {
    moveClock(function (clock) { clock.setUTCDate(clock.getUTCDate() + 1); });
  });
  $("skip-day").addEventListener("click", function () {
    moveClock(function (clock) { clock.setUTCDate(clock.getUTCDate() + 2); });
  });
  $("tonight-23").addEventListener("click", function () {
    moveClock(function (clock) { clock.setUTCHours(23, 0, 0, 0); });
  });
  $("push-provider").addEventListener("click", toggleProvider);
  $("reset-demo").addEventListener("click", function () {
    window.StreakwisePush.reset();
    resetDemo();
    window.setTimeout(function () { refreshProvider().catch(function () {}); }, 1500);
  });
  $("freeze-clock").addEventListener("click", toggleFreeze);
  wireMenu();
  window.setInterval(paintClock, 1000);
  init();
})();
