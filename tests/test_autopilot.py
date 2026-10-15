"""Autopilot: exclusion-aware scene planning, priorities/budgets, similarity refinement and the state machine."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from pipeline.autochunk import (
    AutoChunker,
    allocate_budgets,
    normalize_ranges,
    subtract_ranges,
)
from pipeline.autopilot import Autopilot, AutopilotConfig, parse_timestamp, suggest_priority
from pipeline.config import Settings
from pipeline.db import DB
from pipeline.refine import FrameInfo, RefineConfig, Refiner, refine_selection


# ----------------------------------------------------------------------------- helpers
@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = DB(root / "state.db")
        settings = Settings(video_dir=root / "videos", cache_dir=root / "cache", finished_dir=root / "finished")
        settings.ensure_dirs()
        yield db, settings


def add_video(db: DB, vid: str = "v1", duration: float = 600.0) -> None:
    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, '/fake/video.mp4', 'Video.mp4', 'video', 'new', ?)",
            (vid, duration),
        )


def chunker_with_cuts(db: DB, settings: Settings, cuts: list[float]) -> AutoChunker:
    indexer = MagicMock()
    indexer.get_or_build_filmstrip.return_value = {"cuts": cuts, "thumbnails": []}
    return AutoChunker(db, settings, indexer=indexer)


# ----------------------------------------------------------------------------- ranges and budgets
def test_normalize_ranges_clamps_sorts_and_merges():
    out = normalize_ranges([(100, 200), (150, 260), (-5, 10), (590, 900), (300, 300), ["x", "y"]], 600.0)
    assert out == [(0.0, 10.0), (100.0, 260.0), (590.0, 600.0)]


def test_subtract_ranges():
    assert subtract_ranges(15, 585, [(100, 200), (500, 700)]) == [(15, 100), (200, 500)]
    assert subtract_ranges(0, 100, []) == [(0, 100)]
    assert subtract_ranges(0, 100, [(0, 100)]) == []


def test_allocate_budgets_sums_to_100_and_respects_priority():
    durations = [100.0, 100.0, 100.0, 100.0]
    pcts = allocate_budgets(durations, ["high", "normal", "low", "skip"])
    assert pcts[3] == 0.0
    assert pcts[0] > pcts[1] > pcts[2] > 0
    assert round(sum(pcts), 1) == 100.0
    # Same durations, all normal: an even split, identical to the legacy duration-proportional behaviour
    assert allocate_budgets(durations) == [25.0, 25.0, 25.0, 25.0]


def test_allocate_budgets_rejects_all_skipped_and_unknown_priority():
    with pytest.raises(ValueError):
        allocate_budgets([10.0, 20.0], ["skip", "skip"])
    with pytest.raises(ValueError):
        allocate_budgets([10.0], ["urgent"])


def test_parse_timestamp_and_config_ranges():
    assert parse_timestamp(90) == 90.0
    assert parse_timestamp("1:30") == 90.0
    assert parse_timestamp("1:02:03") == 3723.0
    assert parse_timestamp("12.5") == 12.5
    with pytest.raises(ValueError):
        parse_timestamp("soon")

    cfg = AutopilotConfig(exclude_ranges=["0:00-1:30", [120, "2:30"], {"start": "5:00", "end": 330}, "10 to 20"])
    assert [(r.start_s, r.end_s) for r in cfg.exclude_ranges] == [(0, 90), (120, 150), (300, 330), (10, 20)]
    with pytest.raises(ValueError):
        AutopilotConfig(exclude_ranges=["90-30"])
    assert "confirm_destructive" not in cfg.stored()


def test_suggest_priority_thresholds():
    assert suggest_priority(None, True) == "normal"
    assert suggest_priority(0.9, False) == "normal"
    assert suggest_priority(0.9, True) == "high"
    assert suggest_priority(0.2, True) == "normal"
    assert suggest_priority(0.0, True) == "low"


# ----------------------------------------------------------------------------- scene planning
def test_plan_scenes_never_crosses_excluded_ranges(env):
    db, settings = env
    add_video(db, duration=600.0)
    chunker = chunker_with_cuts(db, settings, [60.0, 150.0, 240.0, 360.0, 480.0])

    scenes = chunker.plan_scenes("v1", exclude_ranges=[(200.0, 300.0)])

    assert scenes, "expected scenes"
    for a, b in scenes:
        assert b - a >= 10.0
        assert b <= 200.0 or a >= 300.0, f"scene ({a}, {b}) overlaps the excluded range"
    # The cut at 240 lies inside the exclusion and must not become a boundary
    assert all(abs(a - 240.0) > 1e-6 and abs(b - 240.0) > 1e-6 for a, b in scenes)
    assert any(abs(b - 200.0) < 0.02 for _, b in scenes) and any(abs(a - 300.0) < 0.02 for a, _ in scenes)


def test_plan_scenes_without_exclusions_matches_legacy_partition(env):
    db, settings = env
    add_video(db, duration=600.0)
    chunker = chunker_with_cuts(db, settings, [60.0, 150.0, 240.0, 360.0, 480.0])
    scenes = chunker.plan_scenes("v1")
    assert scenes[0][0] == 15.0 and scenes[-1][1] == 585.0  # intro/outro trimmed on long videos
    assert all(abs(scenes[i][1] - scenes[i + 1][0]) < 1e-6 for i in range(len(scenes) - 1))


def test_plan_scenes_splits_long_cutless_stretch(env):
    db, settings = env
    add_video(db, duration=1500.0)
    scenes = chunker_with_cuts(db, settings, []).plan_scenes("v1")
    assert max(b - a for a, b in scenes) <= 360.0 + 1e-6
    assert len(scenes) <= 15


def test_plan_scenes_caps_at_15_without_merging_across_exclusions(env):
    db, settings = env
    add_video(db, duration=4000.0)
    cuts = [float(c) for c in range(100, 4000, 100)]
    excl = [(1000.0, 1100.0), (2000.0, 2100.0)]
    scenes = chunker_with_cuts(db, settings, cuts).plan_scenes("v1", exclude_ranges=excl)
    assert len(scenes) <= 15
    for a, b in scenes:
        for ea, eb in excl:
            assert b <= ea + 0.02 or a >= eb - 0.02


def test_plan_scenes_everything_excluded_returns_nothing(env):
    db, settings = env
    add_video(db, duration=600.0)
    assert chunker_with_cuts(db, settings, []).plan_scenes("v1", exclude_ranges=[(0, 600)]) == []


def test_generate_balanced_chunks_default_priority_and_apply_priorities(env):
    db, settings = env
    add_video(db, duration=600.0)
    chunker = chunker_with_cuts(db, settings, [60.0, 150.0, 240.0, 360.0, 480.0])
    chunks = chunker.generate_balanced_chunks("v1", target_frames=300, exclude_ranges=[(200, 300)])
    assert all(c["priority"] == "normal" for c in chunks)
    assert round(sum(c["budget_pct"] for c in chunks), 1) == 100.0

    ids = [c["id"] for c in chunks]
    updated = chunker.apply_priorities("v1", {ids[0]: "skip", ids[1]: "high"})
    by_id = {c["id"]: c for c in updated}
    assert by_id[ids[0]]["budget_pct"] == 0.0 and by_id[ids[0]]["priority"] == "skip"
    assert by_id[ids[1]]["priority"] == "high"
    assert round(sum(c["budget_pct"] for c in updated), 1) == 100.0
    assert by_id[ids[1]]["budget_pct"] > by_id[ids[2]]["budget_pct"] * 1.5 or len(ids) < 3

    with pytest.raises(ValueError):
        chunker.apply_priorities("v1", {999999: "high"})
    with pytest.raises(ValueError):
        chunker.apply_priorities("v1", {cid: "skip" for cid in ids})


def test_save_user_chunks_keeps_priority_and_zeroes_skipped_budget(env):
    db, settings = env
    add_video(db, duration=600.0)
    chunker = chunker_with_cuts(db, settings, [60.0, 150.0, 240.0, 360.0, 480.0])
    chunks = chunker.generate_balanced_chunks("v1", target_frames=300)
    rows = [{k: c[k] for k in ("id", "start_s", "end_s", "budget_pct", "rule", "notes")} for c in chunks]
    rows[0]["priority"] = "skip"
    saved = chunker.save_user_chunks("v1", rows)
    assert saved[0]["priority"] == "skip" and saved[0]["budget_pct"] == 0.0
    assert round(sum(c["budget_pct"] for c in saved), 1) == 100.0

    # A client that does not know about priorities must not reset the stored ones
    saved2 = chunker.save_user_chunks("v1", [{k: c[k] for k in ("id", "start_s", "end_s", "budget_pct")} for c in saved])
    assert saved2[0]["priority"] == "skip"

    with pytest.raises(ValueError):
        chunker.save_user_chunks("v1", [{**rows[0], "priority": "urgent"}])


# ----------------------------------------------------------------------------- refinement (pure)
def unit(*vals: float) -> np.ndarray:
    v = np.array(vals, dtype=np.float32)
    return v / np.linalg.norm(v)


def frame(fid, chunk, emb, *, role="pick", state="keep", rank=0, q=0.5, matched=False, faces=0, sim=0.0, phash=None):
    return FrameInfo(
        id=fid, chunk_id=chunk, t=float(fid), role=role, state=state, rank=rank, emb=emb,
        phash=phash, quality=q, matched=matched, face_count=faces, sim=sim,
    )


def test_refine_replaces_near_duplicate_with_best_distinct_runner():
    a = unit(1, 0, 0, 0)
    a_dup = unit(1, 0.01, 0, 0)
    far = unit(0, 1, 0, 0)
    other = unit(0, 0, 1, 0)
    frames = [
        frame(1, 10, a, q=0.9),
        frame(2, 20, a_dup, q=0.6),                          # near-duplicate of 1, lower quality
        frame(3, 10, other, role="runner_up", state="candidate", rank=0, q=0.7),
        frame(4, 20, a_dup, role="runner_up", state="candidate", rank=0, q=0.8),   # also a lookalike of 1
        frame(5, 20, far, role="runner_up", state="candidate", rank=1, q=0.5),
    ]
    cfg = RefineConfig(dup_similarity=0.95)
    out = refine_selection(frames, {10: 1, 20: 1}, cfg, has_target=False)

    kept_ids = {f.id for f in out.kept}
    assert kept_ids == {1, 5}, "the duplicate is replaced by the best runner that is NOT a lookalike"
    assert [(s.drop_id, s.add_id, s.reason) for s in out.swaps] == [(2, 5, "duplicate")]


def test_refine_drops_duplicate_without_replacement_then_fills_from_other_chunk():
    a = unit(1, 0, 0, 0)
    frames = [
        frame(1, 10, a, q=0.9),
        frame(2, 20, a, q=0.5),                              # exact duplicate, chunk 20 has no runner
        frame(3, 10, unit(0, 1, 0, 0), role="runner_up", state="candidate", q=0.6),
    ]
    out = refine_selection(frames, {10: 1, 20: 1}, RefineConfig(dup_similarity=0.95), has_target=False)
    assert out.unreplaced_drops == [2]
    assert {f.id for f in out.kept} == {1, 3}
    assert out.filled == 1 and out.swaps[-1].reason == "fill"


def test_refine_never_drops_starred_frames_and_ignores_rejected_runners():
    a = unit(1, 0, 0, 0)
    frames = [
        frame(1, 10, a, state="star", q=0.1),
        frame(2, 10, a, q=0.9),
        frame(3, 10, unit(0, 1, 0, 0), role="runner_up", state="reject", q=0.9),
    ]
    out = refine_selection(frames, {10: 2}, RefineConfig(dup_similarity=0.95), has_target=False)
    kept = {f.id for f in out.kept}
    assert 1 in kept, "a human star is never dropped for being a duplicate"
    assert 2 not in kept
    assert 3 not in kept, "a rejected runner-up is never promoted"


def test_refine_person_focus_swaps_in_matching_runners_and_purges_other_faces():
    e = [unit(*(1 if i == j else 0 for j in range(8))) for i in range(8)]
    frames = [
        frame(1, 10, e[0], q=0.9, matched=True, faces=1, sim=0.7),
        frame(2, 10, e[1], q=0.6, faces=1, sim=0.05),                       # somebody else
        frame(3, 10, e[2], q=0.5, faces=0),                                 # no face (equipment shot)
        frame(4, 10, e[3], role="runner_up", state="candidate", q=0.4, matched=True, faces=1, sim=0.6),
        frame(5, 10, e[4], role="runner_up", state="candidate", q=0.45, faces=0),
        frame(6, 10, e[5], role="runner_up", state="candidate", q=0.3, matched=True, faces=1, sim=0.5),
    ]
    out = refine_selection(frames, {10: 3}, RefineConfig(person_focus=0.9, dup_similarity=0.99), has_target=True)
    kept = {f.id for f in out.kept}
    reasons = {s.drop_id: s.reason for s in out.swaps}
    assert 2 not in kept and reasons[2] in ("off_target", "person_focus")
    assert 1 in kept
    assert sum(1 for f in out.kept if f.matched) >= 2, "matching runners replace non-matching picks"
    assert len(out.kept) == 3


def test_refine_without_target_person_ignores_person_rules():
    frames = [
        frame(1, 10, unit(1, 0, 0), faces=1, sim=0.05),
        frame(2, 10, unit(0, 1, 0), role="runner_up", state="candidate", matched=True, faces=1, sim=0.7),
    ]
    out = refine_selection(frames, {10: 1}, RefineConfig(person_focus=1.0), has_target=False)
    assert out.swaps == []


def test_refine_phash_duplicates_are_caught_even_with_different_embeddings():
    frames = [
        frame(1, 10, unit(1, 0, 0), q=0.9, phash=0xFFFF0000FFFF0000),
        frame(2, 20, unit(0, 1, 0), q=0.5, phash=0xFFFF0000FFFF0001),   # hamming distance 1
        frame(3, 20, unit(0, 0, 1), role="runner_up", state="candidate", q=0.4, phash=0x0F0F0F0F0F0F0F0F),
    ]
    out = refine_selection(frames, {10: 1, 20: 1}, RefineConfig(dup_similarity=0.99), has_target=False)
    assert {f.id for f in out.kept} == {1, 3}


# ----------------------------------------------------------------------------- refinement (DB)
def seed_selection(db: DB, with_person: bool = False) -> dict[str, list[int]]:
    """Two chunks; chunk 1 has a duplicated pick pair, every chunk has runner-ups."""
    add_video(db, duration=300.0)
    rng = np.random.default_rng(3)
    with db.write() as conn:
        for pos in range(2):
            conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct) VALUES ('v1', ?, ?, ?, 5, 50)",
                (pos, pos * 100.0, pos * 100.0 + 100.0),
            )
    chunk_ids = [c["id"] for c in db.all("SELECT id FROM chunks ORDER BY position")]
    dup = unit(*rng.standard_normal(16))
    ids: dict[str, list[int]] = {"picks": [], "runners": []}
    layout = [  # (chunk index, role, embedding, composite)
        (0, "pick", dup, 0.9), (0, "pick", unit(*rng.standard_normal(16)), 0.8),
        (1, "pick", dup * 1.0, 0.7),                                   # same picture as the first pick
        (1, "pick", unit(*rng.standard_normal(16)), 0.6),
        (0, "runner_up", unit(*rng.standard_normal(16)), 0.5),
        (1, "runner_up", unit(*rng.standard_normal(16)), 0.5),
        (1, "runner_up", unit(*rng.standard_normal(16)), 0.4),
    ]
    with db.write() as conn:
        for i, (ci, role, emb, comp) in enumerate(layout):
            zs = {"composite": comp, "target_person_sim": 0.0, "target_person_matched": False, "face_count": 0}
            cur = conn.execute(
                "INSERT INTO frames (video_id, chunk_id, t, phash, embedding, zs_scores, preview_path, full_path, native) "
                "VALUES ('v1', ?, ?, ?, ?, ?, '/x.webp', '/x.png', 1)",
                (chunk_ids[ci], 10.0 + i * 7, f"{i * 0x1111111111111111 & 0xFFFFFFFFFFFFFFFF:016x}", emb.astype(np.float32).tobytes(), json.dumps(zs)),
            )
            conn.execute(
                "INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, chunk_ids[ci], role, i, "keep" if role == "pick" else "candidate"),
            )
            ids["picks" if role == "pick" else "runners"].append(cur.lastrowid)
    return ids


def test_refiner_applies_swaps_and_reports_before_after(env):
    db, settings = env
    ids = seed_selection(db)

    report = Refiner(db, settings).run("v1", RefineConfig(dup_similarity=0.95, fill_from_other_chunks=True))

    assert report["picks_before"] == 4 and report["picks_after"] == 4
    assert report["before"]["dup_pairs"] == 1 and report["after"]["dup_pairs"] == 0
    assert report["swaps_by_reason"] == {"duplicate": 1}
    assert report["after"]["max_sim"] < report["before"]["max_sim"]

    picks = {r["frame_id"] for r in db.all("SELECT frame_id FROM picks WHERE role='pick' AND state='keep'")}
    assert ids["picks"][2] not in picks, "the lower-quality twin was demoted"
    assert len(picks) == 4 and ids["picks"][0] in picks
    demoted = db.one("SELECT role, state FROM picks WHERE frame_id=?", (ids["picks"][2],))
    assert (demoted["role"], demoted["state"]) == ("runner_up", "candidate")


def test_refiner_is_idempotent(env):
    db, settings = env
    seed_selection(db)
    refiner = Refiner(db, settings)
    refiner.run("v1", RefineConfig(dup_similarity=0.95))
    second = refiner.run("v1", RefineConfig(dup_similarity=0.95))
    assert second["swaps"] == [] and second["after"]["dup_pairs"] == 0


def test_refiner_without_picks_raises(env):
    db, settings = env
    add_video(db)
    with pytest.raises(RuntimeError):
        Refiner(db, settings).run("v1")


# ----------------------------------------------------------------------------- state machine
class FakeJobs:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.submitted: list[tuple[str, str]] = []
        self.extract_calls: list[dict] = []
        self.busy = False
        self.face_engine = MagicMock()

    def broadcast_event(self, kind, data):
        self.events.append((kind, data))

    def _assert_no_active_job(self, video_id):
        if self.busy:
            from pipeline.jobs import JobConflictError
            raise JobConflictError("busy")

    def submit_autopilot_job(self, job_type, video_id, runner):
        self._assert_no_active_job(video_id)
        self.submitted.append((job_type, video_id))
        return len(self.submitted)

    def submit_extract_750_job(self, video_id, options=None):
        self._assert_no_active_job(video_id)
        self.extract_calls.append(options or {})
        return 100 + len(self.extract_calls)


def make_autopilot(env) -> tuple[Autopilot, FakeJobs, DB]:
    db, settings = env
    fake = FakeJobs()
    ap = Autopilot(db, settings, fake)  # type: ignore[arg-type]
    return ap, fake, db


def test_start_validates_person_and_destructive_and_stores_state(env):
    ap, fake, db = make_autopilot(env)
    add_video(db)

    with pytest.raises(ValueError, match="not found"):
        ap.start("v1", AutopilotConfig(target_person_id=42))

    placeholder = db.create_or_update_person("Nobody", embedding=np.zeros(128, dtype=np.float32))
    with pytest.raises(ValueError, match="no enrolled face"):
        ap.start("v1", AutopilotConfig(target_person_id=placeholder["id"]))

    emb = unit(*np.random.default_rng(1).standard_normal(128))
    person = db.create_or_update_person("Hero", embedding=emb)
    state = ap.start("v1", AutopilotConfig(target_person_id=person["id"], exclude_ranges=["0:10-0:20", "0:15-0:30"]))

    assert state["stage"] == "planning" and state["job_id"] == 1
    assert fake.submitted == [("autopilot_plan", "v1")]
    assert state["config"]["exclude_ranges"] == [{"start_s": 10.0, "end_s": 30.0}]  # merged
    assert db.one("SELECT target_person_id FROM videos WHERE id='v1'")["target_person_id"] == person["id"]
    assert ("autopilot_updated", {"video_id": "v1", "stage": "planning", "message": "Planning scenes..."}) in fake.events


def test_start_requires_confirmation_before_replacing_curated_work(env):
    from pipeline.autochunk import DestructiveRechunkError

    ap, fake, db = make_autopilot(env)
    seed_selection(db)
    with pytest.raises(DestructiveRechunkError):
        ap.start("v1", AutopilotConfig())
    assert ap.get_state("v1")["stage"] == "idle" and fake.submitted == []
    ap.start("v1", AutopilotConfig(confirm_destructive=True))
    assert fake.submitted == [("autopilot_plan", "v1")]


def test_start_rejects_excluding_the_whole_video_and_restores_state_on_conflict(env):
    from pipeline.jobs import JobConflictError

    ap, fake, db = make_autopilot(env)
    add_video(db, duration=100.0)
    with pytest.raises(ValueError, match="whole video"):
        ap.start("v1", AutopilotConfig(exclude_ranges=["0-100"]))

    fake.busy = True
    with pytest.raises(JobConflictError):
        ap.start("v1", AutopilotConfig())
    assert ap.get_state("v1")["stage"] == "idle"


def test_approve_applies_priorities_and_queues_extraction(env):
    ap, fake, db = make_autopilot(env)
    add_video(db, duration=600.0)
    chunker = chunker_with_cuts(db, env[1], [60.0, 150.0, 240.0, 360.0, 480.0])
    chunks = chunker.generate_balanced_chunks("v1", target_frames=300)
    ap._set("v1", stage="awaiting_review", config=AutopilotConfig(target_frames=120).stored())

    with pytest.raises(ValueError, match="(?i)at least one scene"):
        ap.approve("v1", {c["id"]: "skip" for c in chunks})
    assert fake.extract_calls == []

    state = ap.approve("v1", {chunks[0]["id"]: "skip", chunks[1]["id"]: "high"})
    assert state["stage"] == "extracting" and state["job_id"] == 101
    assert fake.extract_calls == [{"target_curated": 120}]
    first = db.one("SELECT priority, budget_pct FROM chunks WHERE id=?", (chunks[0]["id"],))
    assert first == {"priority": "skip", "budget_pct": 0.0}


def test_approve_is_only_valid_in_reviewable_stages(env):
    ap, fake, db = make_autopilot(env)
    add_video(db)
    ap._set("v1", stage="planning")
    with pytest.raises(ValueError, match="Nothing to approve"):
        ap.approve("v1")
    ap._set("v1", stage="extracting")
    with pytest.raises(ValueError):
        ap.approve("v1")


def test_extract_finished_chains_into_refine_only_for_autopilot_runs(env):
    ap, fake, db = make_autopilot(env)
    add_video(db)

    ap.on_extract_finished("v1")  # manual extraction: nothing to chain
    assert fake.submitted == []

    ap._set("v1", stage="extracting")
    ap.on_extract_finished("v1")
    assert fake.submitted == [("autopilot_refine", "v1")]
    assert ap.get_state("v1")["stage"] == "refining"


def test_failure_cancel_and_restart_healing(env):
    ap, fake, db = make_autopilot(env)
    add_video(db)
    ap._set("v1", stage="extracting")
    ap.on_job_failed("v1", "boom")
    state = ap.get_state("v1")
    assert state["stage"] == "error" and state["error"] == "boom"

    ap.on_job_failed("v1", "late failure of an unrelated job")  # error is terminal, not overwritten
    assert ap.get_state("v1")["error"] == "boom"

    ap._set("v1", stage="refining")
    ap.on_job_cancelled("v1")
    assert ap.get_state("v1")["stage"] == "cancelled"

    ap._set("v1", stage="extracting")
    assert ap.heal_stale() == 1
    assert ap.get_state("v1")["stage"] == "error"
    assert "restart" in ap.get_state("v1")["error"]


def test_reset_and_idle_state(env):
    ap, fake, db = make_autopilot(env)
    add_video(db)
    assert ap.get_state("v1")["stage"] == "idle"
    ap._set("v1", stage="ready")
    ap.reset("v1")
    assert ap.get_state("v1")["stage"] == "idle"
