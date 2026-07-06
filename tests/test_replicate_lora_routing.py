"""Source-pin regression for services/replicate.py generate_batch LoRA routing.

Bug fixed 2026-07-06 (this PR): when COLLAGE_LORA_WEIGHTS_URL was set to a
Replicate model ref like `yral/tara-lora-v1:VERSION` (the standard output
shape of `ostris/flux-dev-lora-trainer`), generate_batch was passing it as
`lora_weights` to `black-forest-labs/flux-dev`. Base flux-dev silently
produces wrong-identity outputs in that case — verified 2026-07-06 across
four smoke-test attempts (all returned generic western women instead of
Tara). Root cause: ostris-trained LoRAs must be called as the model
directly. flux-dev + `lora_weights` only accepts HF/CivitAI/URL-shaped
references.

These tests source-pin the routing so a future refactor can't collapse
the two branches back into one. Behavioral tests would require mocking
httpx, which is not worth the setup for this single-file regression.
"""

from pathlib import Path

MODULE = Path(__file__).parent.parent / "app" / "services" / "replicate.py"


def _slice_generate_batch() -> str:
    src = MODULE.read_text()
    start = src.find("async def generate_batch(")
    assert start != -1, "generate_batch not found in replicate.py"
    end = src.find("\nasync def ", start + 1)
    end = end if end != -1 else src.find("\ndef ", start + 1)
    return src[start:end] if end != -1 else src[start:]


def test_generate_batch_routes_https_url_to_flux_dev_lora_weights():
    """A URL-shaped LoRA reference (HF repo, CivitAI, safetensors URL)
    goes to `black-forest-labs/flux-dev` with the URL passed as the
    `lora_weights` input parameter. Matches Replicate's documented API."""
    body = _slice_generate_batch()
    # URL detection anchor
    assert 'startswith(("http://", "https://"))' in body, (
        "URL-shape detection removed — routing may collapse"
    )
    # URL branch → flux-dev + lora_weights
    assert '"black-forest-labs/flux-dev"' in body
    assert '"lora_weights": lora_weights_url' in body


def test_generate_batch_routes_replicate_ref_to_the_model_itself():
    """A Replicate model ref (owner/name or owner/name:version) becomes
    the model itself. This is the ostris-trained-LoRA pattern — flux-dev
    is invoked implicitly by the LoRA model, and the LoRA weights are
    already attached at model-build time."""
    body = _slice_generate_batch()
    # Model-ref detection: "/" in value AND not a URL
    assert '"/" in lora_weights_url' in body, (
        "model-ref detection removed — the ostris-LoRA path will regress"
    )
    # The model-ref branch parses the model ref into (model, version)
    # via .partition(":") so both parts route to _run_prediction. This
    # replaced the earlier `model = lora_weights_url` line (see the
    # 2026-07-06 second-pass fix — custom LoRAs need the versioned
    # endpoint, not the shorthand).
    assert 'lora_weights_url.partition(":")' in body


def test_generate_batch_falls_back_to_nano_banana_pro_when_no_lora():
    """No LoRA URL configured → route to nano-banana-pro. Same as before
    the fix — this branch should be untouched."""
    body = _slice_generate_batch()
    assert '"google/nano-banana-pro"' in body
    # Fallback path is the else branch — pinned by presence of the
    # simplest input shape
    assert '{"prompt": prompt}' in body


def test_generate_batch_documents_the_smoke_test_history():
    """The routing decision is load-bearing (the whole product bet rests
    on LoRA identity durability). The docstring/comments should
    reference the 2026-07-06 smoke-test evidence so a future author
    doesn't 'simplify' the routing back into the buggy shape."""
    body = _slice_generate_batch()
    assert "ostris" in body.lower(), (
        "ostris-trainer context missing — future author may not know why"
    )
    assert "2026-07-06" in body, (
        "date anchor missing — history of the fix should stay findable"
    )


def test_generate_batch_splits_version_off_model_ref():
    """The Replicate model ref for a trained LoRA has the shape
    `owner/name:VERSION`. Custom-trained models can only be invoked via
    the VERSIONED endpoint, so generate_batch must split the version
    out and pass both to _run_prediction. Verified 2026-07-06:
    seven smoke-test attempts all failed with 404 when the shorthand
    endpoint was used for a custom LoRA."""
    body = _slice_generate_batch()
    assert 'lora_weights_url.partition(":")' in body, (
        "version split missing — the shorthand endpoint returns 404 "
        "for user-owned models"
    )
    # generate_batch must forward the parsed version to _run_prediction
    assert "version=version" in body, (
        "version arg not forwarded — _run_prediction won't know to "
        "hit the versioned endpoint"
    )


def test_run_prediction_uses_versioned_endpoint_when_version_given():
    """_run_prediction must route to /v1/models/{model}/versions/{ver}
    /predictions when a version is provided. Fallback (unversioned) is
    the shorthand endpoint reserved for official Replicate models."""
    src = MODULE.read_text()
    assert "/versions/{version}/predictions" in src, (
        "versioned endpoint URL missing — custom LoRAs will 404"
    )
    # Backwards compat: shorthand endpoint retained for official models
    assert "/v1/models/{model}/predictions" in src, (
        "official-model shorthand removed — flux-dev + other official "
        "callers will break"
    )
