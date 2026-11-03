// Streakwise learner home page. Renders GET /api/learners/{id}/home.
// All API text goes in through textContent. innerHTML is used only for the constant
// SVG icon strings below, never for data.
(function () {
  "use strict";

  var ICONS = {
    flame: '<path d="M12 2c.5 3.500-1.500 5-3 7-1.300 1.700-2 3.200-2 5.200A5 5 0 0 0 12 19.500a5 5 0 0 0 5-5.300c0-2-1-3.500-2-4.700-.3 1.200-.9 2-1.700 2.300C13.800 8.700 13.500 5 12 2z"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.300 21a1.940 1.940 0 0 0 3.400 0"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    award: '<circle cx="12" cy="9" r="6"/><path d="m8.500 14.500-1.500 7 5-3 5 3-1.500-7"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="m15.500 8.500-2 5-5 2 2-5z"/>',
  };

  function icon(name, size) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name]; // constant markup only
    return svg;
  }

  function $(id) { return document.getElementById(id); }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function fmt(n) { return Number(n).toLocaleString("en-US"); }
  function plural(n, one, many) { return n === 1 ? one : many; }
  function pad2(n) { return String(n).padStart(2, "0"); }

  var DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
  var DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  var STATE_TEXT = {
    done: "completed", frozen: "protected by a freeze", today: "not done yet",
    missed: "missed", future: "upcoming",
  };

  var state = { home: null, scope: "cohort", token: 0, currentId: null };

  function getJSON(url) {
    return fetch(url).then(function (response) {
      if (response.ok) return response.json();
      return response.json().catch(function () { return {}; }).then(function (body) {
        throw new Error(body.message || ("Request failed (" + response.status + ")"));
      });
    });
  }

  function showError(message) {
    $("error-text").textContent = message;
    $("error").hidden = false;
    $("main").setAttribute("aria-busy", "false");
  }
  function clearError() { $("error").hidden = true; }

  // ---------------------------------------------------------------- sections

  function renderHeader(home) {
    var l = home.learner;
    $("avatar").textContent = l.initials;
    $("user-name").textContent = l.display_name;
    $("user-level").textContent = "Level " + l.level;
    var count = home.inbox.length;
    var badge = $("bell-count");
    badge.hidden = count === 0;
    badge.textContent = String(count);
    $("bell").setAttribute(
      "aria-label",
      count === 0 ? "Notifications, no messages" :
        "Notifications, " + count + " " + plural(count, "message", "messages")
    );
  }

  function renderStreak(home) {
    var s = home.streak;
    $("streak-days").textContent = String(s.days);

    var week = $("week");
    week.replaceChildren();
    s.week.forEach(function (status, i) {
      var cell = el("div", "day " + status, DAY_LETTERS[i]);
      cell.setAttribute("role", "listitem");
      cell.setAttribute("aria-label", DAY_NAMES[i] + ": " + STATE_TEXT[status]);
      cell.title = DAY_NAMES[i] + ": " + STATE_TEXT[status];
      week.appendChild(cell);
    });

    var caption;
    if (s.week.indexOf("today") !== -1) {
      caption = s.days > 0 ? "Complete a lesson today to keep your streak." :
        "Complete a lesson today to start a new streak.";
    } else {
      caption = "Today's lesson is done. See you tomorrow.";
    }
    $("streak-caption").textContent = caption;

    var chip = $("freeze-chip");
    chip.textContent = s.freezes_left + " " + plural(s.freezes_left, "freeze", "freezes") + " left";
  }

  function renderXp(home) {
    var l = home.learner;
    var span = l.next_level_xp - l.level_start_xp;
    var fraction = span > 0 ? Math.min(1, Math.max(0, (l.xp - l.level_start_xp) / span)) : 0;
    var circumference = 2 * Math.PI * 46;
    var ring = $("ring-progress");
    ring.setAttribute("stroke-dasharray", circumference.toFixed(2));
    ring.setAttribute("stroke-dashoffset", (circumference * (1 - fraction)).toFixed(2));
    $("ring-text").textContent = "Lv " + l.level;
    $("ring").setAttribute(
      "aria-label", "Level " + l.level + ", " + Math.round(fraction * 100) + " percent to level " + (l.level + 1)
    );
    $("xp-number").textContent = fmt(l.xp);
    $("xp-next").textContent = fmt(l.xp_to_next) + " XP to level " + (l.level + 1);
    $("ruleset-chip").textContent = "Ruleset " + l.ruleset;
  }

  function progressBar(progress, target, label) {
    var bar = el("div", "bar");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", label);
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", String(target));
    bar.setAttribute("aria-valuenow", String(progress));
    var fill = el("span");
    fill.style.width = (target > 0 ? Math.min(100, (progress / target) * 100) : 0) + "%";
    bar.appendChild(fill);
    return bar;
  }

  function weeklyTile(q) {
    var tile = el("article", "quest weekly");
    tile.appendChild(el("div", "quest-label", "Weekly quest"));
    tile.appendChild(el("h3", "quest-title", q.title));
    tile.appendChild(progressBar(q.progress, q.target, q.title + " progress"));
    var foot = el("div", "quest-foot");
    foot.appendChild(el("span", null, q.progress + " of " + q.target + " lessons"));
    var right = [];
    if (q.status === "completed") right.push("Complete");
    if (q.xp_reward !== null && q.xp_reward !== undefined) right.push("+" + q.xp_reward + " XP");
    if (right.length) foot.appendChild(el("span", "quest-reward", right.join(" - ")));
    tile.appendChild(foot);
    return tile;
  }

  function comebackTile(q) {
    var tile = el("article", "quest comeback");
    var top = el("div", "quest-top");
    top.appendChild(el("div", "quest-label", "Comeback quest"));
    var chipText = q.status === "completed" ? "Complete" :
      (q.days_left === null || q.days_left === undefined) ? "" :
        q.days_left + " " + plural(q.days_left, "day", "days") + " left";
    if (chipText) top.appendChild(el("span", "chip chip-amber", chipText));
    tile.appendChild(top);
    // UI-only wording: the engine stores no quest description.
    tile.appendChild(el("p", null, "Finish a short practice lesson to bounce back from your checkpoint."));
    tile.appendChild(progressBar(q.progress, q.target, "Comeback quest progress"));
    var foot = el("div", "quest-foot");
    foot.appendChild(el("span", null, q.progress + " of " + q.target + " " + plural(q.target, "lesson", "lessons")));
    if (q.xp_reward !== null && q.xp_reward !== undefined) {
      foot.appendChild(el("span", "quest-reward", "+" + q.xp_reward + " XP"));
    }
    tile.appendChild(foot);
    return tile;
  }

  function renderQuests(home) {
    var list = $("quest-list");
    list.replaceChildren();
    home.quests.forEach(function (q) {
      if (q.kind === "weekly") list.appendChild(weeklyTile(q));
      else if (q.kind === "remediation") list.appendChild(comebackTile(q));
    });
    if (!list.children.length) list.appendChild(el("p", "empty", "No quests right now. New ones will show up here."));
  }

  function renderBadges(home) {
    var list = $("badge-list");
    list.replaceChildren();
    home.badges.forEach(function (b) {
      var item = el("div", "badge " + (b.earned ? "earned" : "locked"));
      item.setAttribute("role", "listitem");
      var circle = el("div", "badge-circle");
      circle.appendChild(icon(b.earned ? "award" : "lock", 32));
      item.appendChild(circle);
      var name = el("div", "badge-name", b.name);
      if (!b.earned) name.appendChild(el("span", "sr-only", " (locked)"));
      item.appendChild(name);
      list.appendChild(item);
    });
    if (!list.children.length) list.appendChild(el("p", "empty", "No badges yet. Keep learning to earn your first."));
  }

  function renderLeaderboard(home) {
    var lb = home.leaderboard;
    var rows = state.scope === "cohort" ? lb.cohort_rows : lb.global_rows;
    $("lb-subtitle").textContent = state.scope === "cohort" ? (lb.cohort_name || "No cohort yet") : "All learners";
    ["cohort", "global"].forEach(function (scope) {
      var button = $("seg-" + scope);
      var active = scope === state.scope;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });

    var list = $("lb-rows");
    list.replaceChildren();
    var previous = 0;
    rows.forEach(function (row) {
      if (previous && row.rank > previous + 1) {
        var gap = el("li", "lb-gap", "...");
        gap.setAttribute("aria-hidden", "true");
        list.appendChild(gap);
      }
      previous = row.rank;
      var item = el("li", "lb-row" + (row.is_you ? " you" : ""));
      item.appendChild(el("span", "lb-rank", String(row.rank)));
      item.appendChild(el("span", "lb-name", row.is_you ? "You" : row.display_name));
      item.appendChild(el("span", "lb-xp", fmt(row.xp) + " XP"));
      list.appendChild(item);
    });
    if (!rows.length) list.appendChild(el("li", "empty", "No rankings yet this week."));
    $("lb-footnote").textContent = lb.tie_rule;
  }

  var KIND_STYLE = {
    streak_risk: { cls: "danger", icon: "flame" },
    quest_update: { cls: "amber", icon: "target" },
    remediation: { cls: "amber", icon: "compass" },
  };

  function pad(hour) { return pad2(hour) + ":00"; }

  function renderInbox(home) {
    var list = $("inbox-list");
    list.replaceChildren();
    home.inbox.forEach(function (m) {
      var style = KIND_STYLE[m.kind] || { cls: "", icon: "bell" };
      var item = el("li", "msg " + style.cls);
      var iconBox = el("span", "msg-icon");
      iconBox.appendChild(icon(style.icon, 20));
      item.appendChild(iconBox);
      var text = el("div");
      text.appendChild(el("div", "msg-title", m.title));
      text.appendChild(el("div", "msg-body", m.body));
      item.appendChild(text);
      list.appendChild(item);
    });
    if (!home.inbox.length) list.appendChild(el("li", "empty", "You're all caught up."));
    var n = home.notification_settings;
    $("inbox-footer").textContent =
      "Quiet hours " + pad(n.quiet_hours.start_hour) + " to " + pad(n.quiet_hours.end_hour) +
      ". Push notifications " + (n.push_enabled ? "on" : "off") + ".";
  }

  function renderAsOf(home) {
    var when = new Date(home.as_of);
    var text = new Intl.DateTimeFormat("en-GB", {
      weekday: "short", day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", timeZone: "UTC", hour12: false,
    }).format(when);
    $("as-of").textContent = "Seeded demo data, as of " + text + " UTC. Not live.";
  }

  function render(home) {
    state.home = home;
    renderAsOf(home);
    renderHeader(home);
    renderStreak(home);
    renderXp(home);
    renderQuests(home);
    renderBadges(home);
    renderLeaderboard(home);
    renderInbox(home);
    $("main").setAttribute("aria-busy", "false");
  }

  // ---------------------------------------------------------------- loading

  function setRefreshing(on) {
    var button = $("refresh");
    button.disabled = on;
    $("refresh-label").textContent = on ? "Refreshing..." : "Refresh";
  }

  function load(id) {
    var token = ++state.token;
    state.currentId = id;
    $("main").setAttribute("aria-busy", "true");
    setRefreshing(true);
    return getJSON("/api/learners/" + encodeURIComponent(id) + "/home").then(
      function (home) {
        if (token !== state.token) return; // a newer selection superseded this one
        setRefreshing(false);
        clearError();
        render(home);
      },
      function (err) {
        if (token !== state.token) return;
        setRefreshing(false);
        showError("Could not load this learner's home page. " + err.message);
      }
    );
  }

  function init() {
    return getJSON("/api/learners").then(function (learners) {
      if (!learners.length) throw new Error("No learners found.");
      var select = $("learner-select");
      select.replaceChildren();
      learners.forEach(function (l) {
        var option = el("option", null, l.display_name);
        option.value = l.id;
        select.appendChild(option);
      });
      clearError();
      return load(learners[0].id);
    }).catch(function (err) {
      showError("Could not load the learner list. " + err.message);
    });
  }

  $("learner-select").addEventListener("change", function (event) { load(event.target.value); });
  $("retry").addEventListener("click", function () {
    if (state.currentId) load(state.currentId); else init();
  });
  ["cohort", "global"].forEach(function (scope) {
    $("seg-" + scope).addEventListener("click", function () {
      state.scope = scope;
      if (state.home) renderLeaderboard(state.home);
    });
  });
  $("refresh").addEventListener("click", function () {
    if (state.currentId) load(state.currentId); else init();
  });

  // Nav pills: smooth-scroll to the section and mark the clicked pill active.
  var ignoreScrollUntil = 0;
  var pills = Array.prototype.slice.call(document.querySelectorAll(".nav .pill"));
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function setActivePill(target) {
    pills.forEach(function (pill) {
      var active = pill.getAttribute("data-target") === target;
      pill.classList.toggle("is-active", active);
      if (active) pill.setAttribute("aria-current", "page"); else pill.removeAttribute("aria-current");
    });
  }

  pills.forEach(function (pill) {
    pill.addEventListener("click", function (event) {
      event.preventDefault();
      var target = pill.getAttribute("data-target");
      var behavior = reduceMotion.matches ? "auto" : "smooth";
      if (target === "main") {
        window.scrollTo({ top: 0, behavior: behavior });
      } else {
        var section = $(target);
        section.scrollIntoView({ behavior: behavior, block: "start" });
        section.focus({ preventScroll: true });
      }
      setActivePill(target);
      ignoreScrollUntil = Date.now() + 1000;
    });
  });

  // In a two-column layout several sections sit near the top, so scrolling cannot say which
  // pill is "current". The pill follows clicks, and returns to Home when back at the top.
  window.addEventListener("scroll", function () {
    // Back at the top means Home, except while a pill-triggered smooth scroll is still starting.
    if (window.scrollY < 40 && Date.now() > ignoreScrollUntil) setActivePill("main");
  }, { passive: true });

  $("bell").addEventListener("click", function () {
    var inbox = $("inbox");
    inbox.scrollIntoView({ block: "start" });
    inbox.focus();
  });

  init();
})();
