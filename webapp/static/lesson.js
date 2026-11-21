// Fractions basics lesson, checkpoint, and demo-mode result wiring.
(function () {
  "use strict";

  // Five one-question lessons. Each is submitted as its own lesson_completed event.
  var LESSONS = [
    { id: "fractions_basics_1", prompt: "Which fraction is equal to one half?", answers: ["1/2", "1/3", "2/3", "3/4"], correct: 0, explanation: "One half means one of two equal parts: 1/2." },
    { id: "fractions_basics_2", prompt: "Which fraction is larger?", answers: ["1/4", "3/4", "1/2", "2/8"], correct: 1, explanation: "Three fourths is larger than one half and one quarter." },
    { id: "fractions_basics_3", prompt: "What is 1/4 + 1/4?", answers: ["1/8", "1/2", "2/8", "1"], correct: 1, explanation: "Two fourths make one half, so 1/4 + 1/4 = 1/2." },
    { id: "fractions_basics_4", prompt: "Which fraction is already in simplest form?", answers: ["2/4", "3/6", "4/8", "3/5"], correct: 3, explanation: "Three and five have no common factor greater than one." },
    { id: "fractions_basics_5", prompt: "A pizza has 8 equal slices. You eat 3. What fraction is left?", answers: ["3/8", "5/8", "1/2", "8/5"], correct: 1, explanation: "Eight slices minus three eaten leaves five of eight slices." }
  ];
  function setCheckpointVisible(visible) {
    var row = document.getElementById("checkpoint-row");
    if (row) row.hidden = !visible;
  }
  function setPracticeVisible(visible) {
    var button = document.getElementById("practice-button");
    if (button) button.hidden = !visible;
  }
  function hasOpenPracticeQuest(home) {
    return home.quests.some(function (q) { return q.kind === "remediation" && q.status !== "completed" && q.status !== "expired"; });
  }
  function setRedoVisible(visible) {
    var button = document.getElementById("redo-button");
    if (button) button.hidden = !visible;
    var note = document.getElementById("redo-note");
    if (note && !visible) note.hidden = true;
  }
  var LESSON_KEY = "demoLessonIndex:";
  function getLessonIndex(id) {
    try {
      var v = parseInt(window.localStorage.getItem(LESSON_KEY + id), 10);
      return v >= 0 && v < LESSONS.length ? v : 0;
    } catch (e) { return 0; }
  }
  function setLessonIndex(id, index) {
    try { window.localStorage.setItem(LESSON_KEY + id, String(index)); } catch (e) {}
  }
  function clearLessonIndexes() {
    try {
      Object.keys(window.localStorage).forEach(function (key) {
        if (key.indexOf(LESSON_KEY) === 0) window.localStorage.removeItem(key);
      });
    } catch (e) {}
  }

  var CHECKPOINT_QUESTIONS = [
    { prompt: "Which fraction is greater than 1/2?", answers: ["2/5", "3/5", "1/4", "4/8"], correct: 1, explanation: "Three fifths is greater than one half." },
    { prompt: "What is 2/6 in simplest form?", answers: ["1/3", "1/2", "2/3", "3/6"], correct: 0, explanation: "Divide the numerator and denominator by 2: 2/6 = 1/3." },
    { prompt: "What is 1/3 + 1/3?", answers: ["1/6", "2/3", "1", "2/6"], correct: 1, explanation: "Two thirds is the sum of two one-third pieces." }
  ];

  // Practice (remediation) lesson: what a learner does to finish a practice quest. It is sent as
  // completion_kind "remediation", the only kind that counts for remediation_v1.
  var PRACTICE = {
    id: "fractions_practice_1",
    questions: [
      { prompt: "Practice: which fraction equals 2/4?", answers: ["1/2", "1/4", "2/2", "4/2"], correct: 0, explanation: "Divide top and bottom by 2: 2/4 = 1/2." },
      { prompt: "Practice: which is bigger, 2/3 or 3/5?", answers: ["2/3", "3/5", "They are equal", "Cannot tell"], correct: 0, explanation: "2/3 is about 0.67 and 3/5 is 0.6." }
    ]
  };
  var DECISION_TEXT = {
    passed: "You passed, so no practice quest is needed.",
    not_first_attempt: "No new practice quest: only your first attempt at a checkpoint can earn one.",
    remediated_within_window: "No new practice quest: you already got one in the last 14 days."
  };

  var REASON_TEXT = {
    learner_not_found: "That learner was not found.",
    outside_late_window: "The event was outside the allowed late-arrival window.",
    duplicate_event_id: "This event was already submitted.",
    duplicate_completion_id: "This lesson completion was already submitted.",
    duplicate_attempt_id: "This checkpoint attempt was already submitted.",
    duplicate_view_id: "This view was already submitted.",
    conflicting_event_id_reuse: "That event ID is already used for different content.",
    concurrency_conflict: "The learner changed at the same time. Please try again.",
    schema_invalid: "The submission details were not valid.",
    unsupported_schema_version: "This event version is not supported.",
    untrusted_producer: "The event source was not trusted.",
    reward_field_in_source_event: "The submission included fields only the server may set.",
    dedup_key_mismatch: "The event could not be verified as unique."
  };
  var PRIYA_ID = "a4b67f76-95be-50c1-8157-55022bf4e70d";

  var state = {
    section: "lesson",
    questions: [LESSONS[0]],
    lessonIndex: 0,
    summaryMode: "submit-lesson",
    index: 0,
    score: 0,
    answered: false,
    learnerId: null,
    learners: [],
    requestInFlight: false,
    retryAction: null
  };

  function $(id) { return document.getElementById(id); }
  function escapeText(value) { return value === undefined || value === null ? "" : String(value); }
  function reasonText(reason) {
    return REASON_TEXT[reason] || ("The server reported: " + escapeText(reason).replace(/_/g, " ") + ".");
  }
  function apiError(response, body) {
    var error = new Error(body && (body.message || body.detail) || ("Request failed (" + response.status + ")"));
    error.status = response.status;
    return error;
  }
  function requestJSON(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw apiError(response, body);
        return body;
      });
    }).catch(function (error) {
      if (error.status === undefined) {
        var networkError = new Error("Network error. Check that the server is running and try again.");
        networkError.status = 0;
        throw networkError;
      }
      throw error;
    });
  }
  function getJSON(url) { return requestJSON(url); }

  function updateURL(learnerId) {
    var url = new URL(window.location.href);
    url.searchParams.set("learner", learnerId);
    window.history.replaceState({}, "", url);
  }
  function setSetupError(message) {
    $("setup-error-text").textContent = message;
    $("setup-error").hidden = false;
    $("lesson-card").hidden = true;
    $("changes-panel").hidden = true;
  }
  function clearSetupError() { $("setup-error").hidden = true; }
  function setRequestBusy(busy) {
    state.requestInFlight = busy;
    $("learner-select").disabled = busy;
    document.querySelectorAll("[data-request-control]").forEach(function (control) {
      control.disabled = busy;
    });
    $("retry-button").disabled = busy;
    $("request-retry-button").disabled = busy;
  }
  function requestErrorText(error) {
    if (error.status === 404) return "Not found (404): the learner or demo route is unavailable.";
    if (error.status === 422) return "The server rejected this request (422): " + error.message;
    if (error.status >= 500) return "The server returned an error (" + error.status + "). Try again.";
    return error.message || "The request could not be completed. Try again.";
  }
  function showRequestError(error, retryAction) {
    state.retryAction = retryAction;
    $("request-error-text").textContent = requestErrorText(error);
    $("request-error").hidden = false;
  }
  function clearRequestError() {
    state.retryAction = null;
    $("request-error").hidden = true;
  }
  function updateHomeLink() {
    var page = new URLSearchParams(window.location.search).get("embed") === "1" ? "demo.html" : "index.html";
    $("home-link").href = page + "?learner=" + encodeURIComponent(state.learnerId);
  }
  function formatClock(value) {
    var date = new Date(value);
    return isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("en-GB", {
      day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
      timeZone: "UTC", hour12: false
    }).format(date) + " UTC";
  }

  function refreshClock() {
    return getJSON("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock").then(function (clock) {
      $("demo-clock").textContent = "Demo clock: " + formatClock(clock.now_utc);
    });
  }

  function setDemoClock(clock) {
    return requestJSON("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ now_utc: clock.toISOString() })
    }).then(refreshClock);
  }

  function currentClock() {
    return getJSON("/api/demo/learners/" + encodeURIComponent(state.learnerId) + "/clock")
      .then(function (clock) { return new Date(clock.now_utc); });
  }

  function moveClock(adjust, retryAction) {
    if (state.requestInFlight) return;
    clearRequestError();
    setRequestBusy(true);
    return currentClock().then(function (clock) {
      adjust(clock);
      return setDemoClock(clock);
    }).then(function () {
      startLesson(getLessonIndex(state.learnerId));
    }).catch(function (error) {
      showRequestError(error, retryAction);
    }).finally(function () {
      setRequestBusy(false);
    });
  }
  function setSameDayEvening() {
    return moveClock(function (clock) {
      clock.setUTCHours(20, 0, 0, 0);
    }, setSameDayEvening);
  }
  function setNextDay() {
    return moveClock(function (clock) {
      clock.setUTCDate(clock.getUTCDate() + 1);
    }, setNextDay);
  }
  function setSkipDay() {
    return moveClock(function (clock) {
      clock.setUTCDate(clock.getUTCDate() + 2);
    }, setSkipDay);
  }
  function resetDemo() {
    if (state.requestInFlight) return;
    clearRequestError();
    setRequestBusy(true);
    return requestJSON("/api/demo/reset", { method: "POST" }).then(function () {
      clearLessonIndexes();
      window.location.reload();
    }).catch(function (error) {
      showRequestError(error, resetDemo);
      setRequestBusy(false);
    });
  }

  function loadLearner(learnerId) {
    if (state.requestInFlight) return Promise.resolve();
    state.learnerId = learnerId;
    state.redoLessonId = null;
    updateURL(learnerId);
    updateHomeLink();
    clearRequestError();
    setRequestBusy(true);
    $("demo-clock").textContent = "Loading demo clock...";
    return getJSON("/api/demo/learners/" + encodeURIComponent(learnerId) + "/clock").then(function (clock) {
      $("demo-clock").textContent = "Demo clock: " + formatClock(clock.now_utc);
      $("lesson-card").hidden = false;
      clearSetupError();
      if (new URLSearchParams(window.location.search).get("mode") === "practice") startPractice();
      else startLesson(getLessonIndex(learnerId));
    }).catch(function (error) {
      if (error.status === 404) {
        setSetupError("Demo routes are unavailable. Start the server with DEMO_MODE=1 and DEMO_SANDBOX_PATH set.");
      } else {
        setSetupError("Could not load this learner's demo clock. " + error.message);
      }
      throw error;
    }).finally(function () {
      setRequestBusy(false);
    });
  }

  function initLearners() {
    if (state.requestInFlight) return;
    setRequestBusy(true);
    return getJSON("/api/learners").then(function (learners) {
      if (!learners.length) throw new Error("No learners found.");
      state.learners = learners;
      var requested = new URLSearchParams(window.location.search).get("learner");
      var selected = learners.some(function (learner) { return learner.id === requested; }) ? requested :
        (learners.some(function (learner) { return learner.id === PRIYA_ID; }) ? PRIYA_ID : learners[0].id);
      var select = $("learner-select");
      select.replaceChildren();
      learners.forEach(function (learner) {
        var option = document.createElement("option");
        option.value = learner.id;
        option.textContent = learner.display_name;
        option.selected = learner.id === selected;
        select.appendChild(option);
      });
      setRequestBusy(false);
      return loadLearner(selected);
    }).catch(function (error) {
      if (error.status === 404) {
        setSetupError("Demo routes are unavailable. Start the server with DEMO_MODE=1 and DEMO_SANDBOX_PATH set.");
      } else if (!error.status) {
        setSetupError("Could not load learners. " + error.message);
      }
      setRequestBusy(false);
    });
  }

  function updateProgress() {
    var total = state.questions.length;
    var current = state.index + 1;
    $("section-label").textContent = state.section === "lesson" ?
      (state.isRedo ? "Repeat of lesson " : "Lesson ") + (state.lessonIndex + 1) + " of " + LESSONS.length :
      state.section === "practice" ? "Practice lesson" : "Checkpoint";
    $("question-count").textContent = "Question " + current + " of " + total;
    $("progress-fill").style.width = (current / total) * 100 + "%";
    $("lesson-progress").setAttribute("aria-valuemax", String(total));
    $("lesson-progress").setAttribute("aria-valuenow", String(current));
  }
  function renderQuestion() {
    var question = state.questions[state.index];
    state.answered = false;
    updateProgress();
    $("question-screen").hidden = false;
    $("summary-screen").hidden = true;
    setCheckpointVisible(false);
    setRedoVisible(false);
    setPracticeVisible(false);
    $("question-prompt").textContent = question.prompt;
    $("feedback").hidden = true;
    $("feedback").classList.remove("is-wrong");
    $("continue-button").disabled = true;
    $("continue-button").textContent = "Continue";
    var list = $("answer-list");
    list.replaceChildren();
    question.answers.forEach(function (answer, answerIndex) {
      var label = document.createElement("label");
      label.className = "answer-option";
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "answer";
      input.value = String(answerIndex);
      input.addEventListener("change", function () { selectAnswer(answerIndex); });
      label.appendChild(input);
      label.appendChild(document.createTextNode(answer));
      list.appendChild(label);
    });
    $("question-prompt").focus();
  }
  function selectAnswer(answerIndex) {
    if (state.answered) return;
    state.answered = true;
    var question = state.questions[state.index];
    var correct = answerIndex === question.correct;
    if (correct) state.score += 1;
    Array.prototype.forEach.call(document.querySelectorAll(".answer-option"), function (option, index) {
      option.querySelector("input").disabled = true;
      if (index === question.correct) option.classList.add("is-correct");
      if (index === answerIndex && !correct) option.classList.add("is-wrong");
    });
    $("feedback-title").textContent = correct ? "Correct!" : "Not quite.";
    $("feedback-explanation").textContent = question.explanation;
    $("feedback").classList.toggle("is-wrong", !correct);
    $("feedback").hidden = false;
    $("continue-button").disabled = false;
    $("continue-button").focus();
  }
  function showSummary() {
    var total = state.questions.length;
    var isLesson = state.section === "lesson";
    var isPractice = state.section === "practice";
    $("question-screen").hidden = true;
    $("summary-screen").hidden = false;
    $("section-label").textContent = isLesson ? "Lesson complete" : isPractice ? "Practice complete" : "Checkpoint complete";
    $("question-count").textContent = "Finished";
    $("progress-fill").style.width = "100%";
    $("lesson-progress").setAttribute("aria-valuemax", String(total));
    $("lesson-progress").setAttribute("aria-valuenow", String(total));
    $("summary-title").textContent = isLesson ? "Nice work!" : isPractice ? "Practice done!" : "Checkpoint results";
    $("summary-score").textContent = state.score + " of " + total + " correct";
    state.summaryMode = isLesson ? "submit-lesson" : isPractice ? "submit-practice" : "submit-checkpoint";
    setCheckpointVisible(false);
    setRedoVisible(false);
    setPracticeVisible(false);
    // More than half wrong earns no XP and does not count (src/state_machine/pass_mark.py).
    var failedMark = (isLesson || isPractice) && 2 * (total - state.score) > total;
    $("summary-message").textContent = failedMark ?
      "More than half of the answers are wrong, so this attempt earns no XP and does not count toward your streak or quests. Try the lesson again to earn it." :
      isLesson ? (state.isRedo ?
      "This repeats a lesson you already finished. It is still saved, but repeats count toward the scripted-farming rule (4 in 24 h)." :
      "Finish the lesson to earn XP.") :
      isPractice ? "Finish to complete your practice quest." :
      (state.score >= 2 ? "You passed the checkpoint." : "You need 2 correct answers to pass.");
    $("summary-button").textContent = isLesson ? (state.isRedo ? "Finish lesson (repeat)" : "Finish lesson") :
      isPractice ? "Finish practice lesson" : "Submit checkpoint";
    $("summary-button").disabled = false;
    $("summary-button").focus();
  }

  function questKey(quest) { return quest.kind + ":" + quest.title; }
  function questMap(home) {
    var map = {};
    home.quests.forEach(function (quest) { map[questKey(quest)] = quest; });
    return map;
  }
  function rank(home) {
    var rows = home.leaderboard.global_rows || [];
    var mine = rows.filter(function (row) { return row.is_you; })[0];
    return mine ? mine.rank : null;
  }
  function renderChanges(before, after, result) {
    var items = [];
    var xpGain = after.learner.xp - before.learner.xp;
    if (xpGain) items.push("XP: " + (xpGain > 0 ? "+" : "") + xpGain);
    if (after.streak.days !== before.streak.days) items.push("Streak days: " + before.streak.days + " → " + after.streak.days);
    if (after.streak.freezes_left !== before.streak.freezes_left) items.push("Freezes: " + before.streak.freezes_left + " → " + after.streak.freezes_left);

    var beforeQuests = questMap(before);
    questMap(after);
    after.quests.forEach(function (quest) {
      var old = beforeQuests[questKey(quest)];
      if (!old && quest.kind === "remediation") {
        items.push("Remediation quest appeared: " + quest.title + (quest.days_left === null ? "" : " (" + quest.days_left + " days left)"));
      } else if (old && (old.progress !== quest.progress || old.status !== quest.status)) {
        items.push("Quest progress: " + quest.title + " " + old.progress + " → " + quest.progress + " of " + quest.target);
      }
    });

    var beforeBadges = {};
    before.badges.forEach(function (badge) { beforeBadges[badge.name] = badge.earned; });
    after.badges.forEach(function (badge) {
      if (!beforeBadges[badge.name] && badge.earned) items.push("Badge earned: " + badge.name);
    });

    var oldRank = rank(before);
    var newRank = rank(after);
    if (oldRank !== newRank && newRank !== null) items.push("Leaderboard rank: " + (oldRank === null ? "unranked" : oldRank) + " → " + newRank);

    var oldInbox = {};
    before.inbox.forEach(function (message) { oldInbox[message.created_at + ":" + message.title] = true; });
    var newMessages = after.inbox.filter(function (message) {
      return !oldInbox[message.created_at + ":" + message.title];
    });
    if (newMessages.length) items.push("New inbox items: " + newMessages.length);

    if (result && result.status !== "accepted") {
      items.unshift((result.status === "duplicate" ? "Not submitted (duplicate): " : "Not submitted: ") + reasonText(result.reason));
    }
    if (result && result.xp_withheld === "below_pass_mark") {
      items.unshift("No XP for this lesson: more than half of the answers were wrong. It does not count toward your streak or quests; pass it to earn the full XP.");
    } else if (result && result.xp_withheld) {
      items.unshift("No XP for this lesson: XP is on hold for 24 h after a scripted-farming flag.");
    }
    if (result && result.abuse && result.abuse.new_rules && result.abuse.new_rules.length) {
      items.push("Abuse flag raised (flag-only, XP kept): " + result.abuse.new_rules.join(", "));
    }

    var list = $("changes-list");
    list.replaceChildren();
    if (!items.length) items.push("Nothing changed.");
    items.forEach(function (item) {
      var li = document.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    });
    $("result-xp").textContent = String(before.learner.xp);
    $("result-streak").textContent = String(before.streak.days);
    $("result-metrics").hidden = false;
    $("result-metrics").classList.toggle(
      "is-animated",
      before.learner.xp !== after.learner.xp || before.streak.days !== after.streak.days
    );
    $("changes-panel").hidden = false;
    animateNumber($("result-xp"), before.learner.xp, after.learner.xp);
    animateNumber($("result-streak"), before.streak.days, after.streak.days);
    $("changes-panel").scrollIntoView({ block: "nearest" });
  }

  function animateNumber(element, start, end) {
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.textContent = String(end);
    if (reduceMotion || start === end) return;
    var started = null;
    function tick(timestamp) {
      if (started === null) started = timestamp;
      var progress = Math.min(1, (timestamp - started) / 500);
      var eased = 1 - Math.pow(1 - progress, 3);
      element.textContent = String(Math.round(start + (end - start) * eased));
      if (progress < 1) window.requestAnimationFrame(tick);
    }
    window.requestAnimationFrame(tick);
  }

  function submissionError(error, retryAction) {
    showRequestError(error, retryAction);
  }
  function submitLesson() {
    if (state.requestInFlight) return;
    var id = state.learnerId;
    clearRequestError();
    setRequestBusy(true);
    return getJSON("/api/learners/" + encodeURIComponent(id) + "/home").then(function (before) {
      return requestJSON("/api/demo/learners/" + encodeURIComponent(id) + "/lesson-complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lesson_id: LESSONS[state.lessonIndex].id, completion_kind: "lesson",
          questions_correct: state.score, questions_total: state.questions.length })
      }).then(function (result) {
        return getJSON("/api/learners/" + encodeURIComponent(id) + "/home").then(function (after) {
          renderChanges(before, after, result);
          var failedMark = result.status === "accepted" && result.xp_withheld === "below_pass_mark";
          if (result.status === "accepted" && !failedMark) {
            state.redoLessonId = LESSONS[state.lessonIndex].id;
            setLessonIndex(id, (state.lessonIndex + 1) % LESSONS.length);
          }
          var wasRedo = state.isRedo;
          state.isRedo = false;
          state.summaryMode = "next-lesson";
          // A failed attempt keeps the learner on the same lesson so they can pass it.
          $("summary-button").textContent = failedMark ? "Try this lesson again" : "Next lesson";
          $("summary-message").textContent = failedMark ?
            "Not passed: more than half of the answers were wrong, so no XP. Try the same lesson again." :
            result.status !== "accepted" ?
            "This lesson was not saved. See what changed below." : wasRedo ?
            "Repeat saved. It counts toward the abuse rules (see below)." :
            "Lesson saved. Start the next lesson or try the checkpoint.";
          setCheckpointVisible(true);
          setRedoVisible(!!state.redoLessonId);
          setPracticeVisible(hasOpenPracticeQuest(after));
          if (wasRedo) showRedoProgress(id);
        });
      });
    }).catch(function (error) {
      submissionError(error, submitLesson);
    }).finally(function () {
      setRequestBusy(false);
    });
  }
  // Redo: the same lesson again as a new completion. Each redo is accepted (it is a real new
  // completion) but raises the lesson's repeat count, which the scripted-farming abuse rule
  // counts (4 repeats in 24 h, abuse rules v3). Under the decay rule sets each repeat also earns less XP.
  function showRedoProgress(id) {
    var note = $("redo-note");
    return getJSON("/api/demo/admin/learners/" + encodeURIComponent(id) + "/abuse/live").then(function (live) {
      var r = live.repeats;
      var farming = live.flags.by_rule["scripted_farming.repeat"] || 0;
      note.hidden = false;
      note.classList.toggle("is-flagged", r.count >= r.threshold || farming > 0);
      note.textContent = "Repeats in the last " + r.window_hours + " h: " + r.count + " of " + r.threshold +
        " before the scripted-farming flag" + (farming ? " (flagged " + farming + "x)" : "") + ".";
    }).catch(function () { note.hidden = true; });
  }
  // Redo replays the lesson's questions; finishing it sends the same lesson_id again.
  function redoLesson() {
    if (state.requestInFlight || !state.redoLessonId) return;
    var index = 0;
    LESSONS.forEach(function (lesson, i) { if (lesson.id === state.redoLessonId) index = i; });
    startLesson(index);
    state.isRedo = true;
    updateProgress();
  }
  function startPractice() {
    $("changes-panel").hidden = true;
    clearRequestError();
    state.section = "practice";
    state.isRedo = false;
    state.questions = PRACTICE.questions;
    state.index = 0;
    state.score = 0;
    renderQuestion();
  }
  function submitPractice() {
    if (state.requestInFlight) return;
    var id = state.learnerId;
    clearRequestError();
    setRequestBusy(true);
    return getJSON("/api/learners/" + encodeURIComponent(id) + "/home").then(function (before) {
      return requestJSON("/api/demo/learners/" + encodeURIComponent(id) + "/lesson-complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lesson_id: PRACTICE.id, completion_kind: "remediation",
          questions_correct: state.score, questions_total: state.questions.length })
      }).then(function (result) {
        return getJSON("/api/learners/" + encodeURIComponent(id) + "/home").then(function (after) {
          renderChanges(before, after, result);
          state.summaryMode = "back-to-lessons";
          $("summary-button").textContent = "Back to lessons";
          $("summary-message").textContent = result.status !== "accepted" ? "This practice lesson was not saved." :
            hasOpenPracticeQuest(before) && !hasOpenPracticeQuest(after) ? "Practice quest complete. Nice recovery!" :
            "Practice saved. (Only an open practice quest is moved forward by it.)";
        });
      });
    }).catch(function (error) {
      submissionError(error, submitPractice);
    }).finally(function () {
      setRequestBusy(false);
    });
  }
  function showPolicyDecision(id, attemptPassed) {
    // What the engine decided about this attempt (stored as a fact, M4).
    return getJSON("/api/demo/admin/learners/" + encodeURIComponent(id) + "/policy").then(function (p) {
      var d = p.decisions[0];
      if (!d) return;
      var text = d.decision === "assign" ?
        "You got a practice quest: finish one practice lesson within 7 days." :
        (DECISION_TEXT[d.reason] || d.reason);
      $("summary-message").textContent = (attemptPassed ? "You passed the checkpoint. " : "You did not pass. ") + text;
    }).catch(function () {});
  }
  function submitCheckpoint() {
    if (state.requestInFlight) return;
    var id = state.learnerId;
    clearRequestError();
    setRequestBusy(true);
    return getJSON("/api/learners/" + encodeURIComponent(id) + "/home").then(function (before) {
      return requestJSON("/api/demo/learners/" + encodeURIComponent(id) + "/checkpoint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ assessment_id: $("checkpoint-select").value, passed: state.score >= 2 })
      }).then(function (result) {
        return getJSON("/api/learners/" + encodeURIComponent(id) + "/home").then(function (after) {
          renderChanges(before, after, result);
          state.summaryMode = "back-to-lessons";
          $("summary-button").textContent = "Back to lessons";
          setPracticeVisible(hasOpenPracticeQuest(after));
          if (result.status === "accepted") return showPolicyDecision(id, state.score >= 2);
        });
      });
    }).catch(function (error) {
      submissionError(error, submitCheckpoint);
    }).finally(function () {
      setRequestBusy(false);
    });
  }
  function continueQuestion() {
    if (!state.answered) return;
    if (state.index < state.questions.length - 1) {
      state.index += 1;
      renderQuestion();
    } else {
      showSummary();
    }
  }
  function startLesson(index) {
    state.section = "lesson";
    state.isRedo = false;
    state.lessonIndex = index;
    state.questions = [LESSONS[index]];
    state.index = 0;
    state.score = 0;
    clearRequestError();
    $("changes-panel").hidden = true;
    renderQuestion();
  }
  function startCheckpoint() {
    $("changes-panel").hidden = true;
    state.section = "checkpoint";
    state.questions = CHECKPOINT_QUESTIONS;
    state.index = 0;
    state.score = 0;
    renderQuestion();
  }

  $("learner-select").addEventListener("change", function (event) {
    loadLearner(event.target.value).catch(function () {});
  });
  $("retry-button").addEventListener("click", function () { initLearners(); });
  $("request-retry-button").addEventListener("click", function () {
    if (state.retryAction && !state.requestInFlight) state.retryAction();
  });
  $("continue-button").addEventListener("click", continueQuestion);
  $("summary-button").addEventListener("click", function () {
    if (state.summaryMode === "submit-lesson") submitLesson();
    else if (state.summaryMode === "submit-checkpoint") submitCheckpoint();
    else if (state.summaryMode === "submit-practice") submitPractice();
    else startLesson(getLessonIndex(state.learnerId));
  });
  var checkpointButton = document.getElementById("checkpoint-button");
  if (checkpointButton) checkpointButton.addEventListener("click", startCheckpoint);
  var redoButton = document.getElementById("redo-button");
  if (redoButton) redoButton.addEventListener("click", redoLesson);
  $("practice-button").addEventListener("click", startPractice);
  // The scenario card with its own clock buttons was removed (the split view's top bar has
  // them); only wire these up if a page still has them.
  [["same-day-button", setSameDayEvening], ["next-day-button", setNextDay],
   ["skip-day-button", setSkipDay], ["reset-demo-button", resetDemo]].forEach(function (pair) {
    var button = document.getElementById(pair[0]);
    if (button) button.addEventListener("click", pair[1]);
  });
  initLearners();
})();
