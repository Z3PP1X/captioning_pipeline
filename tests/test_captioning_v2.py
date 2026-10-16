"""Phase G - Captioning v2: versioned prompts, temperature, validation gate, trigger profiles, captions table,
cost logging and the caption API routes. Temp DB + fixture frames + mocked HTTP only (never the video library)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import pipeline.api as api
from pipeline import prompts
from pipeline.captions import caption_stats, get_caption, import_caption, record_caption
from pipeline.config import DEFAULT_USER_SETTINGS, Settings
from pipeline.db import DB
from pipeline.export import Exporter, existing_caption_kind
from pipeline.jobs import JobManager
from pipeline.openrouter import (
    CaptionValidationError,
    ModelRefusalError,
    OpenRouterCaptioner,
    accumulate_usage,
    validate_caption_gate,
)
from pipeline.reconcile import reconcile_captions_and_state

VID = "fixture_phase_g"
SLUG = "fixture_g"
TRIGGER = "restrained_elegance"
GOOD = (
    f"{TRIGGER} Photograph of an adult woman kneeling on a polished concrete floor. "
    + "Unbleached hemp rope wraps her upper arms in a neat box tie with even wraps and a firm cinch. " * 6
).strip()


# ----------------------------------------------------------------------------- fixtures
@pytest.fixture()
def env(tmp_path: Path):
    settings = Settings(
        video_dir=tmp_path / "videos",
        cache_dir=tmp_path / "cache",
        finished_dir=tmp_path / "proj" / "data" / "finished",
    )
    settings.ensure_dirs()
    return settings, DB(settings.db_path)


def _seed(db: DB, settings: Settings, n_picks: int = 2) -> list[dict]:
    frames_dir = settings.cache_dir / "candidates" / VID
    frames_dir.mkdir(parents=True, exist_ok=True)
    out = []
    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration, title_hash) VALUES (?, ?, ?, ?, 'curating_300', 60.0, 'v_hash0001')",
            (VID, "/nonexistent/fixture_g.mp4", "Fixture G.mp4", SLUG),
        )
        cid = conn.execute(
            "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, dirty) VALUES (?, 0, 0, 60, 5, 100, 0)",
            (VID,),
        ).lastrowid
        for i in range(n_picks):
            t = 1.0 + i
            png = frames_dir / f"full_{i}.png"
            cv2.imwrite(str(png), np.full((64, 96, 3), 40 + 10 * i, dtype=np.uint8))
            fid = conn.execute(
                "INSERT INTO frames (video_id, chunk_id, t, metrics, zs_scores, full_path) VALUES (?, ?, ?, '{}', '{}', ?)",
                (VID, cid, t, str(png)),
            ).lastrowid
            conn.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, 'pick', ?, 'keep')", (fid, cid, i))
            out.append({"id": fid, "t": t})
    return out


def _stem(frame: dict) -> str:
    return f"{SLUG}_c01_{int(frame['t'] * 1000):07d}"


def _run_job(db: DB, settings: Settings, fake_caption, options: dict | None = None) -> tuple[JobManager, int, list]:
    jm = JobManager(db, settings)
    held: list = []
    events: list = []
    jm.caption_executor.submit = lambda fn, *args: held.append((fn, args))
    jm.broadcast_event = lambda kind, data: events.append((kind, data))
    jm.captioner.caption_single_frame = fake_caption
    job_id = jm.submit_openrouter_caption_job(VID, {"api_key": "k", "mode": "concept", "trigger": TRIGGER, **(options or {})})
    for fn, args in held:
        fn(*args)
    jm.executor.shutdown(wait=False)
    return jm, job_id, events


def _fake_http(monkeypatch, reply_for_model):
    """Patch httpx so each request gets `reply_for_model(model) -> (content, finish_reason, usage)`."""
    sent: list[dict] = []

    class Reply:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    async def fake_post(self, url, json=None, **_kw):
        sent.append(json)
        content, finish, usage = reply_for_model(json["model"])
        return Reply({"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": usage})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return sent


# ----------------------------------------------------------------------------- 1. versioned prompts
def test_prompts_come_from_the_repo_not_gemini():
    src = (Path(__file__).resolve().parents[1] / "src" / "pipeline" / "openrouter.py").read_text(encoding="utf-8")
    assert ".gemini" not in src and "/mnt/c" not in src
    for mode in prompts.MODES:
        assert (prompts.PROMPT_DIR / f"{mode}.md").is_file()


def test_prompt_version_is_stable_and_per_mode():
    versions = {m: prompts.prompt_version(m) for m in prompts.MODES}
    assert versions["concept"].startswith(f"concept-{prompts.PROMPT_VERSION}-")
    assert len(set(versions.values())) == 3
    assert prompts.prompt_version("concept") == versions["concept"]
    assert prompts.prompt_version("unknown-mode") == versions["concept"]  # unknown modes fall back to concept


def test_system_and_user_prompts_match_mode_ranges_and_rules():
    for mode in prompts.MODES:
        lo, hi = prompts.MODE_WORD_RANGES[mode]
        system = prompts.render_system_prompt(mode, "trg_x", "woman, ropes")
        user = prompts.render_user_prompt(mode, "trg_x", "woman, ropes")
        assert f"between {lo} and {hi} words" in system
        assert f"{lo} to {hi} words" in user
        assert "trg_x Photograph of" in system and "trg_x Photograph of" in user
        assert "21+" in system  # adults-only rule in every mode
        assert "{" not in system and "{" not in user  # every placeholder rendered
    # Character mode uses the primary class noun as the subject reference
    assert "trg_x Photograph of a woman..." in prompts.render_system_prompt("character", "trg_x", "woman, ropes")


def test_payload_is_mode_specific_and_requests_usage():
    captioner = OpenRouterCaptioner()
    payload = captioner.build_request_payload(
        "b64", "sys", user_text=captioner.load_user_prompt("character", TRIGGER, "woman")
    )
    assert payload["usage"] == {"include": True}
    assert "80 to 140 words" in payload["messages"][1]["content"][0]["text"]


# ----------------------------------------------------------------------------- 2. temperature
def test_default_temperature_is_point_four_everywhere():
    assert DEFAULT_USER_SETTINGS["openrouter_temperature"] == 0.4
    assert api.OpenRouterCaptionRequest().temperature == 0.4
    assert OpenRouterCaptioner.build_request_payload("b64", "sys")["temperature"] == 0.4
    web = Path(__file__).resolve().parents[1] / "src" / "web" / "app" / "features" / "modals" / "openrouter-modal.component.ts"
    assert "signal<number>(0.4)" in web.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- 3. validation gate
def _gate(raw: str, mode: str = "concept") -> list[str]:
    cleaned, _ = OpenRouterCaptioner.sanitize_and_validate_caption(raw, mode=mode, trigger=TRIGGER, video_hash="v_hash0001")
    return validate_caption_gate(raw, cleaned, mode=mode, trigger=TRIGGER, video_hash="v_hash0001")


def test_gate_accepts_a_clean_caption():
    assert _gate(GOOD) == []


@pytest.mark.parametrize("raw, reason", [
    ("Here is a detailed caption: " + GOOD, "preamble"),
    ("This image shows an adult woman. " + GOOD, "preamble"),
    (GOOD + " I can't describe anything further.", "residue"),
    (f"{TRIGGER} Photograph of an adult woman kneeling in rope.", "too short"),
    (GOOD + " " + GOOD + " " + GOOD, "too long"),
    (GOOD.replace("adult woman", "teenage woman"), "minor-coded"),
    (GOOD + " Her wrists are bound and", "complete sentence"),
])
def test_gate_rejects(raw, reason):
    reasons = _gate(raw)
    assert any(reason in r for r in reasons), reasons


def test_girl_is_rewritten_to_adult_woman():
    cleaned, warnings = OpenRouterCaptioner.sanitize_and_validate_caption(
        GOOD.replace("an adult woman", "a girl"), trigger=TRIGGER
    )
    assert "girl" not in cleaned and "adult woman" in cleaned
    assert any("girl" in w for w in warnings)


def test_gated_reply_raises_validation_error_and_counts_usage(monkeypatch):
    _fake_http(monkeypatch, lambda model: ("Here is the caption you asked for: " + GOOD, "stop",
                                           {"prompt_tokens": 900, "completion_tokens": 200, "cost": 0.0012}))
    usage: dict = {}

    async def go():
        async with httpx.AsyncClient() as client:
            await OpenRouterCaptioner().caption_single_frame(
                client=client, img_bgr=np.zeros((64, 64, 3), dtype=np.uint8), api_key="k", trigger=TRIGGER, usage=usage
            )

    with pytest.raises(CaptionValidationError) as err:
        asyncio.run(go())
    assert any("preamble" in r for r in err.value.reasons)
    assert usage["calls"] == 1 and usage["prompt_tokens"] == 900 and usage["completion_tokens"] == 200
    assert usage["cost_usd"] == pytest.approx(0.0012)
    assert usage["prompt_version"] == prompts.prompt_version("concept")


# ----------------------------------------------------------------------------- 4. trigger profiles
def test_character_profile_checks_class_noun_identity_and_single_trigger():
    raw = (f"{TRIGGER} Photograph of a person with almond eyes standing by a wall. {TRIGGER} wears leather cuffs "
           "on both wrists and a posture collar. " * 3)
    _, warnings = OpenRouterCaptioner.sanitize_and_validate_caption(raw, mode="character", trigger=TRIGGER, class_noun="woman, ropes")
    assert any("Class noun 'woman'" in w for w in warnings)
    assert any("almond eyes" in w for w in warnings)
    assert any("more than once" in w for w in warnings)
    # Concept mode has no class-noun requirement and the trigger/hash order is unchanged for every mode
    cleaned, warnings = OpenRouterCaptioner.sanitize_and_validate_caption(GOOD, mode="concept", trigger=TRIGGER, video_hash="v_h", class_noun="woman")
    assert cleaned.split()[:2] == [TRIGGER, "v_h"]
    assert not any("Class noun" in w for w in warnings)
    assert prompts.expected_lead("character", "c1_eva", "woman, ropes") == "c1_eva Photograph of a woman"


# ----------------------------------------------------------------------------- 5/6. caption job, captions table, cost
def test_job_cascades_gate_failures_and_records_captions_with_cost(env, monkeypatch):
    settings, db = env
    frames = _seed(db, settings, n_picks=2)

    def reply(model):
        usage = {"prompt_tokens": 1000, "completion_tokens": 250, "cost": 0.002}
        if "gemma" in model:
            return "Sure! Here is a caption: " + GOOD, "stop", usage  # rejected by the gate -> layer 2
        return GOOD, "stop", usage

    sent = _fake_http(monkeypatch, reply)
    jm = JobManager(db, settings)
    held, events = [], []
    jm.caption_executor.submit = lambda fn, *args: held.append((fn, args))
    jm.broadcast_event = lambda kind, data: events.append((kind, data))
    job_id = jm.submit_openrouter_caption_job(VID, {"api_key": "k", "mode": "concept", "trigger": TRIGGER})
    for fn, args in held:
        fn(*args)
    jm.executor.shutdown(wait=False)

    assert [p["model"] for p in sent].count("google/gemma-4-31b-it") == 2
    assert all(p["temperature"] == 0.4 for p in sent)
    job = db.one("SELECT status, error FROM jobs WHERE id=?", (job_id,))
    assert job["status"] == "finished" and job["error"] is None
    for frame in frames:
        row = get_caption(db, frame["id"])
        assert row["source"] == "vlm" and row["layer"] == "layer2"
        assert row["model"] == "deepseek/deepseek-v4.1-flash"
        assert row["prompt_version"] == prompts.prompt_version("concept")
        assert row["text"].split()[:2] == [TRIGGER, "v_hash0001"]
        assert row["word_count"] == len(row["text"].split())
        # Both attempts (the rejected L1 reply and the accepted L2 reply) are billed
        assert row["cost_usd"] == pytest.approx(0.004)
        assert row["prompt_tokens"] == 2000 and row["completion_tokens"] == 500
        assert row["job_id"] == job_id
        assert any("Recovered via layer2" in w for w in row["warnings"])
        txt = settings.finished_dir / "per_video" / SLUG / f"{_stem(frame)}.txt"
        assert txt.read_text(encoding="utf-8") == row["text"]
    finished = [d for k, d in events if k == "job_finished"][-1]
    assert finished["exported"] == 2 and finished["failed"] == 0
    assert finished["cost_usd"] == pytest.approx(0.008)
    assert finished["api_calls"] == 4 and finished["layers"]["layer2"] == 2 and finished["rejected"]["layer1"] == 2
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "exported"


def test_frame_rejected_by_every_layer_is_a_per_frame_failure(env):
    settings, db = env
    frames = _seed(db, settings, n_picks=2)

    async def fake_caption(img_bgr, model, usage=None, **kwargs):
        accumulate_usage(usage, {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001}})
        if img_bgr[0, 0, 0] == 50:  # the second frame: every layer fails
            if "grok" in model:
                raise CaptionValidationError("rejected", reasons=["too short"])
            raise ModelRefusalError("refused", model=model)
        return GOOD, []

    _, job_id, events = _run_job(db, settings, fake_caption)
    job = db.one("SELECT status, error FROM jobs WHERE id=?", (job_id,))
    assert job["status"] == "finished"
    assert "1 of 2 frames failed" in job["error"] and "all 3 cascade layers rejected" in job["error"]
    assert "Cost $0.0040" in job["error"]  # 1 call for the good frame + 3 for the failed one
    assert get_caption(db, frames[0]["id"])["source"] == "vlm"
    assert get_caption(db, frames[1]["id"]) is None
    # No PNG is written for a frame that never got a caption
    assert not (settings.finished_dir / "per_video" / SLUG / f"{_stem(frames[1])}.png").exists()
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "curating_300"
    finished = [d for k, d in events if k == "job_finished"][-1]
    assert finished["failed"] == 1 and finished["api_calls"] == 4


def test_job_reuses_db_caption_and_rewrites_its_txt(env):
    settings, db = env
    frames = _seed(db, settings, n_picks=1)
    with db.write() as conn:
        record_caption(conn, frames[0]["id"], VID, GOOD, model="m", layer="layer1", mode="concept")
    per_video = settings.finished_dir / "per_video" / SLUG
    per_video.mkdir(parents=True)
    (per_video / f"{_stem(frames[0])}.txt").write_text("restrained_elegance, rope", encoding="utf-8")
    (per_video / f"{_stem(frames[0])}.json").write_text('{"caption_source": "stub"}', encoding="utf-8")
    billed: list = []

    async def fake_caption(**kwargs):
        billed.append(1)
        return GOOD, []

    _run_job(db, settings, fake_caption)
    assert billed == []
    assert (per_video / f"{_stem(frames[0])}.txt").read_text(encoding="utf-8") == GOOD
    assert existing_caption_kind(per_video / f"{_stem(frames[0])}.txt") == "caption"
    assert (per_video / f"{_stem(frames[0])}.png").is_file()


# ----------------------------------------------------------------------------- 5. reconcile import + exporter
def test_reconcile_imports_txt_captions_idempotently(env):
    settings, db = env
    frames = _seed(db, settings, n_picks=3)
    per_video = settings.finished_dir / "per_video" / SLUG
    per_video.mkdir(parents=True)
    (per_video / f"{_stem(frames[0])}.txt").write_text(GOOD, encoding="utf-8")
    (per_video / f"{_stem(frames[1])}.txt").write_text("restrained_elegance, rope, chains", encoding="utf-8")
    (per_video / f"{_stem(frames[1])}.json").write_text('{"caption_source": "stub"}', encoding="utf-8")
    (per_video / f"{_stem(frames[2])}.txt").write_text("old caption from disk that differs.", encoding="utf-8")
    with db.write() as conn:
        record_caption(conn, frames[2]["id"], VID, GOOD, model="m", layer="layer1")

    first = reconcile_captions_and_state(db, settings)
    assert first["captions_imported"] == 2
    assert get_caption(db, frames[0]["id"])["source"] == "imported"
    assert get_caption(db, frames[0]["id"])["trigger"] == TRIGGER
    assert get_caption(db, frames[1]["id"])["source"] == "stub"
    assert get_caption(db, frames[2]["id"])["text"] == GOOD  # a VLM row is never replaced by a file

    second = reconcile_captions_and_state(db, settings)
    assert second["captions_imported"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM captions")["n"] == 3


def test_exporter_writes_txt_from_captions_table_and_records_stubs(env):
    settings, db = env
    frames = _seed(db, settings, n_picks=2)
    with db.write() as conn:
        record_caption(conn, frames[0]["id"], VID, GOOD, model="m", layer="layer1")

    res = Exporter(db, settings).export_video(VID)
    per_video = settings.finished_dir / "per_video" / SLUG
    assert res["captions_kept"] == 1 and res["stubs_written"] == 1
    assert (per_video / f"{_stem(frames[0])}.txt").read_text(encoding="utf-8") == GOOD
    assert (settings.finished_dir / "merged" / f"{_stem(frames[0])}.txt").read_text(encoding="utf-8") == GOOD
    stub = get_caption(db, frames[1]["id"])
    assert stub["source"] == "stub"

    # A stub row never displaces a caption, and a later VLM caption replaces the stub row
    with db.write() as conn:
        assert import_caption(conn, frames[0]["id"], VID, "x, y", source="stub") is False
        record_caption(conn, frames[1]["id"], VID, GOOD, model="m2", layer="layer1")
    Exporter(db, settings).export_video(VID)
    assert (per_video / f"{_stem(frames[1])}.txt").read_text(encoding="utf-8") == GOOD
    assert existing_caption_kind(per_video / f"{_stem(frames[1])}.txt") == "caption"
    assert get_caption(db, frames[0]["id"])["source"] == "vlm"


# ----------------------------------------------------------------------------- 6. API routes
@pytest.fixture()
def client(env, monkeypatch):
    settings, db = env
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "db", db)
    return TestClient(api.app)


def test_frame_caption_route(env, client):
    settings, db = env
    frames = _seed(db, settings, n_picks=2)
    with db.write() as conn:
        record_caption(conn, frames[0]["id"], VID, GOOD, model="m", layer="layer1", mode="concept",
                       prompt_version="concept-g1-x", cost_usd=0.001, prompt_tokens=10, completion_tokens=5,
                       warnings=["w1"])

    res = client.get(f"/api/frames/{frames[0]['id']}/caption")
    assert res.status_code == 200
    body = res.json()["caption"]
    assert body["text"] == GOOD and body["source"] == "vlm" and body["warnings"] == ["w1"]
    assert client.get(f"/api/frames/{frames[1]['id']}/caption").json() == {"frame_id": frames[1]["id"], "caption": None}
    assert client.get("/api/frames/999999/caption").status_code == 404


def test_caption_stats_route(env, client):
    settings, db = env
    frames = _seed(db, settings, n_picks=3)
    with db.write() as conn:
        record_caption(conn, frames[0]["id"], VID, GOOD, model="google/gemma-4-31b-it", layer="layer1",
                       prompt_version="concept-g1-a", cost_usd=0.002, prompt_tokens=100, completion_tokens=50, job_id=7)
        record_caption(conn, frames[1]["id"], VID, GOOD, model="x-ai/grok-4.7", layer="layer3",
                       prompt_version="concept-g1-a", cost_usd=0.003, prompt_tokens=200, completion_tokens=60, job_id=8)
        import_caption(conn, frames[2]["id"], VID, GOOD)

    res = client.get("/api/captions/stats")
    assert res.status_code == 200
    stats = res.json()
    assert stats["total_captions"] == 3
    assert stats["total_cost_usd"] == pytest.approx(0.005)
    assert stats["prompt_tokens"] == 300 and stats["completion_tokens"] == 110
    assert stats["by_source"] == {"vlm": 2, "imported": 1, "stub": 0}
    models = {(m["model"], m["layer"]): m for m in stats["by_model"]}
    assert models[("x-ai/grok-4.7", "layer3")]["cost_usd"] == pytest.approx(0.003)
    assert stats["by_prompt_version"] == [{"prompt_version": "concept-g1-a", "count": 2}]
    assert stats["current_prompt_versions"]["style"] == prompts.prompt_version("style")

    by_job = client.get("/api/captions/stats", params={"job_id": 8}).json()
    assert by_job["total_captions"] == 1 and by_job["total_cost_usd"] == pytest.approx(0.003)
    assert caption_stats(db, video_id="nope")["total_captions"] == 0
