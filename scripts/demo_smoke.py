"""End-to-end smoke check of the whole demo (M1 to M8) against a throw-away sandbox.

    python scripts/demo_smoke.py

Prints one line per check (PASS name / FAIL name: detail) and a final count; exit code 1 when any check
failed. Writes docs/demo/smoke_results.json and docs/demo/smoke_output.txt with the key numbers seen.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DEMO_MODE"] = "1"
os.environ["DEMO_SANDBOX_PATH"] = tempfile.mkdtemp(prefix="demo_smoke_")

from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402

client = TestClient(app)
PROFILE = json.loads((ROOT / "webapp" / "demo_data" / "ui_profile.json").read_text())
ID = {info["display_name"]: lid for lid, info in PROFILE["learners"].items()}
SEED_CLOCK = "2026-10-16T18:00:00Z"

CHECKS: list[dict] = []
NUMBERS: dict[str, str] = {}
SECTION = ["intro"]


def check(name: str, ok: bool, detail: str = "", fact: str | None = None) -> bool:
    CHECKS.append({"section": SECTION[0], "name": name, "ok": bool(ok), "detail": detail})
    if fact:
        NUMBERS[name] = fact
    print(("PASS " + name) if ok else f"FAIL {name}: {detail}")
    return bool(ok)


def get(url):
    return client.get(url)


def post(url, body=None):
    return client.post(url, json={} if body is None else body)


def admin(who):
    return f"/api/demo/admin/learners/{ID[who]}"


def snap(who):
    return get(f"{admin(who)}/snapshot").json()


def set_clock(who, iso):
    return post(f"/api/demo/learners/{ID[who]}/clock", {"now_utc": iso})


def js_admin(expr: str, tz: str = "Asia/Kolkata"):
    """Evaluate a JS expression against the real admin.js (window.Admin) under a non-UTC machine timezone."""
    prog = (
        "global.window = {}; global.document = {addEventListener(){}, getElementById(){return null;}};"
        "require(process.argv[1]); const Admin = window.Admin; console.log(JSON.stringify(" + expr + "));"
    )
    out = subprocess.run(["node", "-e", prog, str(ROOT / "webapp" / "static" / "admin.js")],
                         capture_output=True, text=True, env={**os.environ, "TZ": tz}, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-300:])
    return json.loads(out.stdout)


def intro():
    SECTION[0] = "intro"
    r = get("/api/demo/personas")
    check("personas_endpoint_returns_12", r.status_code == 200 and len(r.json()) == 12, f"status {r.status_code}", "12 personas")
    check("priya_home_200", get(f"/api/learners/{ID['Priya Nair']}/home").status_code == 200)
    before = snap("Priya Nair")
    r = post(f"/api/demo/learners/{ID['Priya Nair']}/lesson-complete", {"lesson_id": "smoke_intro_lesson"})
    after = snap("Priya Nair")
    check("priya_lesson_accepted_xp_up", r.json()["status"] == "accepted" and after["xp_total"] > before["xp_total"],
          f"{r.json()} {before} {after}", f"XP {before['xp_total']} -> {after['xp_total']} (+{after['xp_total'] - before['xp_total']})")
    check("priya_streak_per_engine", after["streak"] >= before["streak"], f"{before['streak']} -> {after['streak']}",
          f"streak {before['streak']} -> {after['streak']} (she already practised on the demo day, so it holds)")


def m1_m2():
    SECTION[0] = "M1/M2"
    who = "Ishaan Gupta"
    base = f"{admin(who)}/lab"
    j = post(f"{base}/fresh-lesson").json()
    ev = get(f"{admin(who)}/events?limit=10").json()
    newest = ev["events"][0] if ev["events"] else {}
    check("events_show_ids", bool(newest.get("correlation_id")) and any(e["type"] == "LessonCompleted" and e["fields"].get("source_event_id") for e in ev["events"]),
          "no correlation or source event ids", f"{ev['total']} events; newest {newest.get('type')} has correlation id {str(newest.get('correlation_id'))[:8]}")
    check("fresh_lesson_accepted", j["result"]["status"] == "accepted" and j["after"]["xp_total"] > j["before"]["xp_total"], str(j["result"]),
          f"accepted, XP {j['before']['xp_total']} -> {j['after']['xp_total']}")
    j = post(f"{base}/duplicate").json()
    check("duplicate_rejected_no_xp_change", j["result"]["status"] == "duplicate" and j["before"]["xp_total"] == j["after"]["xp_total"], str(j["result"]),
          f"reason {j['result']['reason']}, XP unchanged at {j['after']['xp_total']}")
    j = post(f"{base}/late", {"hours_late": 24}).json()
    check("late_24h_accepted", j["result"]["status"] == "accepted", str(j["result"]), f"accepted, XP {j['before']['xp_total']} -> {j['after']['xp_total']}")
    j = post(f"{base}/late", {"hours_late": 100}).json()
    check("late_100h_rejected", j["result"]["reason"] == "outside_late_window", str(j["result"]), f"reason {j['result']['reason']}, XP unchanged at {j['after']['xp_total']}")
    j = post(f"{base}/rebuild-compare").json()
    check("rebuild_compare_identical", j["identical"] is True, str(j), f"identical, {j['events_replayed']} events replayed")
    rs = get(f"{base}/ruleset").json()
    new = [v for v in rs["available"] if v != rs["effective"]][0]
    post(f"{base}/ruleset", {"version": new})
    post(f"{base}/fresh-lesson")
    xp_events = [e for e in get(f"{admin(who)}/events?limit=30").json()["events"] if e["type"] == "XPAwarded"]
    check("ruleset_switch_only_new_events", xp_events[0]["rule_version"] == new and xp_events[-1]["rule_version"] == rs["effective"],
          str([e["rule_version"] for e in xp_events]), f"newest reward {xp_events[0]['rule_version']}, older rewards keep {xp_events[-1]['rule_version']}")


def m3():
    SECTION[0] = "M3"
    who = "Priya Nair"
    c = get("/api/demo/admin/cohorts").json()
    check("cohorts_have_members", bool(c["cohorts"]) and all(x["members"] for x in c["cohorts"]), str(c)[:200],
          f"{len(c['cohorts'])} cohorts for {c['epoch']}, {sum(x['member_count'] for x in c['cohorts'])} learners")
    cons = get("/api/demo/admin/cohorts/consistency").json()
    check("consistency_lag_zero", cons["lag"] == 0, str(cons), f"projection offset {cons['projection_offset']} = log head {cons['log_head']}, lag 0")
    p = post("/api/demo/admin/cohorts/latency-probe").json()
    check("latency_probe_numbers", p["queries"] == 400 and p["rank_ms"]["p95"] >= 0, str(p),
          f"p95 rank {p['rank_ms']['p95']} ms, p95 top-N {p['top_ms']['p95']} ms")
    m = post(f"{admin(who)}/cohorts/measure-update").json()
    check("measure_update_timings_and_xp", "ingest" in (m["timings_ms"] or {}) and m["after"]["xp_total"] > m["before"]["xp_total"], str(m),
          f"ingest {m['timings_ms']['ingest']} ms + projection {m['timings_ms']['project']} ms; cohort rank {m['before']['cohort_rank']} -> {m['after']['cohort_rank']}, "
          f"global rank {m['before']['global_rank']} -> {m['after']['global_rank']}, XP +{m['after']['xp_total'] - m['before']['xp_total']}")


def m4():
    SECTION[0] = "M4"
    who = "Tara Bose"
    base = f"{admin(who)}/policy"
    r = post(f"{base}/checkpoint", {"passed": False}).json()
    d = r["latest_decision"]
    check("fail_checkpoint_assigns_quest", d and d["decision"] == "assign", str(r), f"decision assign, quest deadline {d['deadline_at'][:10]}")
    pol = get(base).json()
    check("decision_and_quest_recorded", len(pol["decisions"]) >= 1 and any(q["status"] == "assigned" for q in pol["quests"]), str(pol)[:200],
          f"{len(pol['decisions'])} decision, {len(pol['quests'])} remediation quest (assigned)")
    r = post(f"{base}/checkpoint", {"passed": False}).json()
    d = r["latest_decision"]
    check("second_fail_same_assessment_not_first_attempt", d["decision"] == "no_action" and d["reason"] == "not_first_attempt", str(d), "no_action, reason not_first_attempt")
    r = post(f"{base}/checkpoint", {"passed": False, "assessment_id": "fractions_checkpoint_2"}).json()
    d = r["latest_decision"]
    check("other_assessment_frequency_guardrail", d["decision"] == "no_action" and d["reason"] == "remediated_within_window", str(d), "no_action, reason remediated_within_window (14-day window)")
    r = post(f"{base}/complete-remediation").json()
    pol = get(base).json()
    check("complete_remediation_accepted", r["result"]["status"] == "accepted" and any(q["status"] == "completed" for q in pol["quests"]), str(r),
          "accepted, remediation quest completed")
    v = get(f"{admin('Vikram Rao')}/policy").json()
    check("vikram_has_pending_remediation", any(q["status"] == "assigned" for q in v["quests"]), str(v["quests"]), "open remediation quest (assigned) from the seed")
    r = post("/api/demo/admin/policy/expiry-sweep", {"learner_id": ID["Tara Bose"]})
    check("expiry_sweep_200", r.status_code == 200, r.text, f"swept {r.json()['learners_swept']} learners, {r.json()['expired_count']} expired at the seed clock")


def m5():
    SECTION[0] = "M5"
    arjun, base = "Arjun Reddy", f"{admin('Arjun Reddy')}/notifications"

    def send(who, channel, **kw):
        body = {"channel": channel, "category": "general"}
        body.update(kw)
        return post(f"{admin(who)}/notifications/send", body).json()["facts"]

    f = send(arjun, "in_app")
    check("in_app_delivered", f[0]["status"] == "dispatched", str(f), "in-app message delivered")
    f = send(arjun, "in_app", repeat_same=True)
    check("dedup_second_suppressed", f[1]["reason"] == "duplicate_proposal", str(f), "second copy suppressed: duplicate_proposal")
    f = send(arjun, "push", count=7)
    capped = [x for x in f if x["reason"] == "daily_cap_exceeded"]
    check("daily_cap_suppression", len(capped) >= 1, str(f), f"{len(f)} push sends: {len(f) - len(capped)} delivered, {len(capped)} suppressed daily_cap_exceeded")
    f = send("Ananya Das", "push")
    check("consent_suppression_push_opted_out", f[0]["reason"] == "opt_out", str(f), "Ananya (push off): suppressed opt_out")
    f = send("Diya Menon", "email")
    check("email_delivered", f[0]["status"] == "dispatched", str(f), "Diya: email delivered")
    set_clock(arjun, "2026-10-18T12:00:00Z")  # past the 24 h cap window
    post(f"{base}/outage", {"channel": "push", "active": True})
    f = send(arjun, "push")
    t = get(base).json()
    check("outage_then_dead_letter", f[0]["status"] == "failed" and t["counts"]["dead_letter_open"] == 1, str(f),
          f"3 attempts failed, dead_letter_open {t['counts']['dead_letter_open']}")
    post(f"{base}/outage", {"channel": "push", "active": False})
    rep = post(f"{base}/replay-dlq").json()
    t = get(base).json()
    check("replay_resolves_dead_letter", rep and rep[0]["status"] == "dispatched" and t["counts"]["dead_letter_resolved"] == 1, str(rep),
          f"replayed {len(rep)}, dead_letter_resolved {t['counts']['dead_letter_resolved']}")
    set_clock(arjun, "2026-10-20T23:00:00Z")
    f = send(arjun, "push")
    check("quiet_hours_deferred", f[0]["status"] == "queued" and f[0]["reason"] == "quiet_hours_rescheduled", str(f), f"deferred until {f[0]['rescheduled_for']}")
    until = js_admin(f'Admin.fmtTime("{f[0]["rescheduled_for"]}", true)')
    sent = js_admin('Admin.fmtTime("2026-10-20T23:00:00Z")')
    check("utc_send_time_shows_2300_on_ist_machine", sent == "Oct 20, 23:00:00", sent, f"23:00 UTC send shows {sent} (TZ=Asia/Kolkata)")
    check("utc_deferred_until_0800", "08:00" in until and until.endswith("UTC"), until, f"deferred shows 'until {until}'")
    shorts = js_admin('[Admin.short("attempt:aaaaaaaa-1"), Admin.short("attempt:bbbbbbbb-2"), Admin.short("4871b5d7-1673")]')
    check("short_strips_prefix_distinct", shorts[0] != shorts[1] and shorts[0] == "aaaaaaaa" and shorts[2] == "4871b5d7", str(shorts),
          f"attempt ids shorten to {shorts[0]} / {shorts[1]}")
    set_clock(arjun, "2026-10-21T09:00:00Z")
    rel = post(f"{base}/release-deferred").json()
    check("release_deferred_delivered", rel and rel[0]["status"] == "dispatched", str(rel), "released and delivered at 09:00")
    set_clock(arjun, SEED_CLOCK)


def m6():
    SECTION[0] = "M6"
    expected = [("Kabir Singh", "impossible_timing"), ("Kabir Singh", "scripted_farming"),
                ("Meera Pillai", "replay"), ("Meera Pillai", "duplicate_exploit")]
    types = set()
    for who, pattern in expected:
        a = post(f"{admin(who)}/abuse/attack", {"pattern": pattern}).json()
        s = post("/api/demo/admin/abuse/scan", {"learner_id": ID[who]}).json()
        found = {x["signal_type"] for x in get(f"/api/demo/admin/abuse/signals?learner_id={ID[who]}").json()["signals"]}
        types.add(pattern) if pattern in found else None
        rej = ", ".join(f"{k} {v}" for k, v in a["rejected_by_reason"].items()) or "none"
        check(f"{pattern}_flagged_{who.split()[0].lower()}", pattern in found, f"found {sorted(found)}",
              f"{a['events_sent']} events sent, {a['accepted']} accepted, rejected: {rej}; signal {pattern}")
        check(f"scan_{pattern}_scores_unchanged", s["scores_changed"] is False and s["event_log_before"] == s["event_log_after"], str(s)[:200],
              f"scores_changed false, event log {s['event_log_before']}/{s['event_log_after']}")
    check("four_signal_types_present", types == {p for _, p in expected}, str(types), "replay, impossible_timing, scripted_farming, duplicate_exploit")
    sigs = get("/api/demo/admin/abuse/signals").json()["signals"]
    check("signals_have_evidence", any(x["evidence"] and any(e["resolved"] for e in x["evidence"]) for x in sigs), "no resolved evidence", f"{len(sigs)} signals with resolved evidence")


def m7():
    SECTION[0] = "M7"
    a = post("/api/demo/admin/experiments/assign").json()
    check("assign_counts_add_to_12", a["newly_assigned"] + a["already_assigned"] == 12, str(a),
          f"{a['newly_assigned']} assigned, {a['eligible']} eligible, {a['excluded']} excluded")
    again = post("/api/demo/admin/experiments/assign").json()
    check("assign_again_changes_nothing", again["newly_assigned"] == 0, str(again), "second run: 0 newly assigned")
    rows = get("/api/demo/admin/experiments/assignments").json()
    check("assignments_12_rows_with_arms", len(rows["rows"]) == 12 and all(r["arm"] for r in rows["rows"]), str(rows)[:200],
          f"treatment {rows['arm_counts']['treatment']}, control {rows['arm_counts']['control']}")
    tara_arm = [r for r in rows["rows"] if r["display_name"] == "Tara Bose"][0]["arm"]
    set_clock("Tara Bose", "2026-10-17T20:00:00Z")
    s = post("/api/demo/admin/experiments/sweep", {"learner_id": ID["Tara Bose"]}).json()["outcomes"][0]
    check("sweep_for_tara", s["fact_status"] == "dispatched" and tara_arm == "treatment", str(s),
          f"Tara is {tara_arm}: reminder {s['fact_status']} at 20:00 UTC on a day she has not practised")
    rep = get("/api/demo/admin/experiments/report").json()
    r = rep["report"]
    check("report_valid_with_arms", r["valid"] is True and set(r["arms"]) == {"treatment", "control"}, str(r)[:200],
          f"valid, treatment eligible {r['arms']['treatment']['eligible']}, control eligible {r['arms']['control']['eligible']}, hash {rep['sha256'][:12]}")
    ref = get("/api/demo/admin/experiments/reference-report")
    check("reference_report_ok", ref.status_code == 200, str(ref.status_code))
    ref = ref.json()
    rr, ra = ref["report"], ref["report"]["arms"]
    matured = all(ra[a]["mastery"]["rate"] is not None and ra[a]["mastery"]["denominator"] > 0 for a in ra)
    ci = rr["contrast"]["mastery_ci95"]
    check("reference_report_matured_mastery_ci_hash", rr["valid"] and matured and ci and ci["low"] < ci["high"] and len(ref["sha256"]) == 64
          and rr["srm_assigned"] is not None and "breached" in rr["gaming"], str(ref)[:300],
          f"reference run valid: mastery treatment {ra['treatment']['mastery']['rate']:.3f} vs control {ra['control']['mastery']['rate']:.3f}, "
          f"difference {rr['contrast']['mastery_rate_difference']:.3f}, 95% CI [{ci['low']:.3f}, {ci['high']:.3f}], hash {ref['sha256'][:12]}")
    check("reference_report_deterministic_and_live_untouched", get("/api/demo/admin/experiments/reference-report").json()["sha256"] == ref["sha256"]
          and get("/api/demo/admin/experiments/report").json()["sha256"] == rep["sha256"], "hash changed")
    rp = post("/api/demo/admin/experiments/reproduce").json()
    check("reproduce_identical", rp["identical"] is True, str(rp), f"identical, {rp['hash_1'][:12]} = {rp['hash_2'][:12]}")
    set_clock("Tara Bose", SEED_CLOCK)


def m8():
    SECTION[0] = "M8"
    w = post("/api/demo/admin/recovery/wipe-and-rebuild").json()
    check("wipe_and_rebuild_identical", w["rebuild"]["identical"] is True, str(w)[:300],
          f"{w['rebuild']['events_replayed']} events replayed in {w['rebuild']['duration_ms']} ms, checksum {w['rebuild']['after']['hash'][:12]} identical")
    d = get("/api/demo/admin/recovery/dead-letters").json()
    check("dead_letter_counts", d["resolved"] >= 1 and d["open"] == 0, str(d), f"open {d['open']}, resolved {d['resolved']}")
    t0 = time.time()
    b = post("/api/demo/admin/recovery/benchmark", {"profile": "small"}).json()
    check("small_benchmark_passed", b.get("overall_passed") is True, str(b)[:200], f"overall_passed true in {round(time.time() - t0, 1)} s")


def tick():
    SECTION[0] = "tick"
    r = post("/api/demo/admin/tick").json()
    ok = len(r["steps"]) == 8 and all(s["ok"] for s in r["steps"])
    check("engine_tick_eight_steps", ok, str([(s["name"], s["ok"]) for s in r["steps"]]),
          "8 steps ok: " + ", ".join(s["name"] for s in r["steps"]))
    again = post("/api/demo/admin/tick").json()
    check("engine_tick_idempotent", again["summary"]["new_signals"] == 0 and again["summary"]["new_decisions"] == 0, str(again["summary"]), f"second tick summary {again['summary']}")
    check("priya_home_after_tick", get(f"/api/learners/{ID['Priya Nair']}/home").status_code == 200)


def main() -> int:
    steps = [intro, m1_m2, m3, m4, m5, m6, m7, m8, tick]
    for step in steps:
        try:
            step()
        except Exception as error:  # noqa: BLE001 - report and keep going
            check(f"{step.__name__}_ran", False, f"{type(error).__name__}: {error}")
    failed = [c for c in CHECKS if not c["ok"]]
    summary = f"{len(CHECKS) - len(failed)} passed, {len(failed)} failed, {len(CHECKS)} checks"
    print(summary)
    out_dir = ROOT / "docs" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke_results.json").write_text(
        json.dumps({"summary": summary, "checks": CHECKS, "numbers": NUMBERS}, indent=2) + "\n", encoding="utf-8")
    lines = [("PASS " + c["name"]) if c["ok"] else f"FAIL {c['name']}: {c['detail']}" for c in CHECKS]
    (out_dir / "smoke_output.txt").write_text("\n".join(lines + [summary]) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
