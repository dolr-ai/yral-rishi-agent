"""Video generation — the parts that fail silently if they drift.

One rule: test what fails **silently** and is expensive to discover. Not that
pydantic serialises a model, not that a dict literal has its keys — those pass
whatever the code does and only cost time to read.

Every test below maps to either "this breaks and nothing tells you" or "this
already broke once":

1. workflow injection — a wrong node id produces a *valid* graph that generates
   the wrong thing, or ignores the prompt entirely
2. the error body shape — the app parses one specific shape and falls back to
   dumping raw text at the user for anything else
3. the safety gate failing closed — the one behaviour where a bug is not a bad
   UX but a bad video, permanently public
4. thumbnail extraction — it shipped broken once already (see below)
"""

import asyncio
import json
import pathlib

import pytest

from videogen import comfyui, models, prompt_check

WORKFLOW = pathlib.Path("app/videogen/workflows/ltx2.json")


# ─── the exported graph ─────────────────────────────────────────────────


def test_workflow_file_is_valid_and_has_every_injection_target():
    graph = json.loads(WORKFLOW.read_text())
    for node in (
        comfyui.NODE_IS_TEXT_TO_VIDEO,
        comfyui.NODE_DURATION,
        comfyui.NODE_PROMPT,
        comfyui.NODE_IMAGE,
        comfyui.NODE_SEED_PASS1,
        comfyui.NODE_SEED_PASS2,
    ):
        assert node in graph, f"{node} missing — re-exported graph renamed nodes"


def test_text_to_video_bypasses_the_image_path():
    graph = comfyui.build_workflow(
        prompt="a lighthouse in fog", duration_seconds=5, image_filename=None
    )
    assert graph[comfyui.NODE_IS_TEXT_TO_VIDEO]["inputs"]["value"] is True
    assert graph[comfyui.NODE_PROMPT]["inputs"]["value"] == "a lighthouse in fog"


def test_image_to_video_enables_the_image_path_and_references_the_upload():
    graph = comfyui.build_workflow(
        prompt="make it move", duration_seconds=5, image_filename="abc-source.png"
    )
    assert graph[comfyui.NODE_IS_TEXT_TO_VIDEO]["inputs"]["value"] is False
    assert graph[comfyui.NODE_IMAGE]["inputs"]["image"] == "abc-source.png"


@pytest.mark.parametrize("requested", [600, None])
def test_duration_never_exceeds_the_cap(requested):
    """Over the cap, and absent, both land on the cap."""
    graph = comfyui.build_workflow(
        prompt="x", duration_seconds=requested, image_filename=None
    )
    assert (
        graph[comfyui.NODE_DURATION]["inputs"]["value"] == comfyui.MAX_DURATION_SECONDS
    )


def test_seeds_are_randomised_per_request():
    """The exported graph pins both seeds to 0. Left alone, every generation of
    the same prompt returns an identical video."""
    seeds = set()
    for _ in range(5):
        g = comfyui.build_workflow(prompt="x", duration_seconds=5, image_filename=None)
        seeds.add(
            (
                g[comfyui.NODE_SEED_PASS1]["inputs"]["noise_seed"],
                g[comfyui.NODE_SEED_PASS2]["inputs"]["noise_seed"],
            )
        )
    assert len(seeds) > 1, "seeds are not being randomised"


# ─── the mobile contract ────────────────────────────────────────────────


def test_error_body_is_a_flat_string_value():
    """VideoGenErrorDtoSerializer reads `element[key] as? JsonPrimitive` — a
    nested object throws and the user is shown raw JSON instead of the message.
    """
    from videogen.routes import _error

    payload = json.loads(bytes(_error(400, "InvalidInput", "try again").body))
    assert payload == {"InvalidInput": "try again"}
    assert isinstance(payload["InvalidInput"], str)


# ─── the safety gate ────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _body(prompt="a cat"):
    return models.GenerateRequestBody(prompt=prompt, model_id="ltx2", user_id="u")


def test_prompt_check_refuses_when_the_model_is_unreachable(monkeypatch):
    """Fails closed. A refusal costs the user a retry; a wrongly-approved video
    is public and permanent."""
    from services import llm_registry

    async def boom(**_):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_registry, "call", boom)
    assert _run(prompt_check.is_safe(_body())) is False


def test_prompt_check_refuses_on_an_unparseable_verdict(monkeypatch):
    from services import llm_registry

    async def waffle(**_):
        return type("R", (), {"content": "Well, it depends on context..."})()

    monkeypatch.setattr(llm_registry, "call", waffle)
    assert _run(prompt_check.is_safe(_body())) is False


@pytest.mark.parametrize(
    "verdict,expected", [("SAFE", True), ("safe", True), ("UNSAFE", False)]
)
def test_prompt_check_reads_the_verdict(monkeypatch, verdict, expected):
    from services import llm_registry

    async def answer(**_):
        return type("R", (), {"content": verdict})()

    monkeypatch.setattr(llm_registry, "call", answer)
    assert _run(prompt_check.is_safe(_body())) is expected


def test_prompt_check_sends_the_image_alongside_the_prompt():
    """Image-to-video means the user supplies a picture. A clean prompt over an
    unacceptable image is still an unacceptable video, so both must reach the
    model in one call."""
    body = models.GenerateRequestBody(
        prompt="make it move",
        model_id="ltx2",
        user_id="u",
        image=models.ImagePayload(
            type="Base64",
            value=models.ImageValue(data="aGk=", mime_type="image/png"),
        ),
    )
    content = prompt_check._build_messages(body)[-1]["content"]
    kinds = [part["type"] for part in content]
    assert "text" in kinds and "image_url" in kinds


# ─── thumbnails ─────────────────────────────────────────────────────────


def test_thumbnail_does_not_read_the_video_from_a_pipe():
    """Regression guard. MP4 keeps its `moov` index atom at the end of the
    file, so a decoder must seek backwards after reading it. On stdin it
    cannot, and ffmpeg dies with "Cannot determine format of input after EOF" —
    which meant every generated video got a broken thumbnail while generation
    itself looked fine. Always hand ffmpeg a path.

    Cheap enough to run everywhere, unlike the end-to-end test below.
    """
    import inspect

    from videogen import storage as videogen_storage

    source = inspect.getsource(videogen_storage.extract_thumbnail)
    assert "pipe:0" not in source, "ffmpeg cannot read MP4 from stdin"
    assert "NamedTemporaryFile" in source


@pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None, reason="ffmpeg not installed"
)
def test_thumbnail_extraction_produces_a_png():
    import subprocess
    import tempfile

    from videogen import storage as videogen_storage

    with tempfile.NamedTemporaryFile(suffix=".mp4") as clip:
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=64x64:rate=8:duration=1",
                "-pix_fmt",
                "yuv420p",
                clip.name,
            ],
            check=True,
        )
        video = pathlib.Path(clip.name).read_bytes()

    thumb = asyncio.run(videogen_storage.extract_thumbnail(video))
    assert thumb[:8] == b"\x89PNG\r\n\x1a\n"


def test_storage_keys_match_what_the_app_builds():
    """The app constructs {cdn}/{principal}/{video_id}.mp4 and the
    `-thumbnail.png` sibling itself. These keys are the contract."""
    from videogen import storage as videogen_storage

    assert videogen_storage.video_key("abc", "vid1") == "abc/vid1.mp4"
    assert videogen_storage.thumbnail_key("abc", "vid1") == "abc/vid1-thumbnail.png"


# ─── SpacetimeDB identity ───────────────────────────────────────────────


def test_post_status_is_a_tagged_variant_not_a_string():
    """SATS enums go over the wire as {"draft": []}. A bare "Draft" is
    rejected with `unknown variant` — verified against the live database."""
    from videogen import spacetime

    assert spacetime.STATUS_DRAFT == {"draft": []}
    assert spacetime.STATUS_UPLOADED == {"uploaded": []}


def test_targets_the_reducers_the_app_actually_reads():
    """The Drafts tab reads `posts_3` filtered on `creator_oauth_subject`.
    Only `add_post_2` / `update_post_status_2` write that table — the earlier
    `add_post` and `add_post_v2` wrote tables nothing reads, which is exactly
    how a generated video can be stored and still never appear."""
    import inspect

    from videogen import spacetime

    assert '"add_post_2"' in inspect.getsource(spacetime.add_draft_post)
    assert '"update_post_status_2"' in inspect.getsource(spacetime.publish_post)
