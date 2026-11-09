(function () {
  "use strict";

  var PRIYA_ID = "4871b5d7-1673-5416-8a37-48353d80e6a4";
  var state = { learnerId: null, requestInFlight: false };

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
    return isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }
  function updateURL() {
    var url = new URL(window.location.href);
    url.searchParams.set("learner", state.learnerId);
    window.history.replaceState({}, "", url);
  }
  function frameURL(page) {
    return page + "?embed=1&learner=" + encodeURIComponent(state.learnerId);
  }
  function reloadFrames() {
    $("lesson-frame").src = frameURL("lesson.html");
    $("home-frame").src = frameURL("index.html");
  }
  function refreshClock() {
    return requestJSON("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock")
      .then(function (clock) {
        $("demo-clock").textContent = "Demo clock: " + formatClock(clock.now_utc);
        return new Date(clock.now_utc);
      });
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
    }).then(refreshClock).catch(setError).finally(function () {
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
      reloadFrames();
      return refreshClock();
    }).catch(setError).finally(function () { state.requestInFlight = false; });
  }
  function init() {
    return requestJSON("/api/learners").then(function (learners) {
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
      return refreshClock();
    }).catch(setError);
  }

  $("demo-learner").addEventListener("change", function (event) {
    state.learnerId = event.target.value;
    updateURL();
    reloadFrames();
    refreshClock().catch(setError);
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
  $("reset-demo").addEventListener("click", resetDemo);
  init();
})();
