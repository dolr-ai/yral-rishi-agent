"""The one safety gate on video generation.

The old service ran a separate HMAC-signed moderation service, and staged every
base64 image to object storage first purely because that service could only read
images by URL. A vision-capable LLM reads the prompt and the image together in a
single call, so all of that disappears — no staging bucket, no TTL sweeper, no
second service to keep alive.

Checking the image matters as much as the prompt: image-to-video means the user
supplies a picture, and a clean prompt over an unacceptable image is still an
unacceptable video.

Fails **closed**. If the model is unreachable or answers something we cannot
parse, generation is refused. A generated video is public and permanent; a
retry costs the user seconds.
"""

import logging

from videogen.models import GenerateRequestBody

logger = logging.getLogger(__name__)

LLM_PROCESS = "videogen_prompt_check"

_SYSTEM = (
    "You screen requests for an AI video generator on a mainstream social app. "
    "Decide whether the request is acceptable to generate.\n\n"
    "Reply UNSAFE if the prompt or the image involves: sexual content or "
    "nudity; anyone who appears to be a minor in any sexual or suggestive "
    "framing; real, identifiable people depicted in a false or compromising "
    "way; graphic violence or gore; hateful or extremist content; or clear "
    "promotion of self-harm or illegal activity.\n\n"
    "Otherwise reply SAFE. Creative, strange, dramatic and fictional ideas are "
    "fine — you are screening for harm, not for taste.\n\n"
    'Answer with exactly one word: "SAFE" or "UNSAFE".'
)

# Shown to the user verbatim by the app, so it is written for them rather than
# for a log: it says what to do next and does not accuse.
REJECTION_MESSAGE = "We can't create a video from this. Try describing something else."


def _build_messages(body: GenerateRequestBody) -> list[dict]:
    """One user turn carrying the prompt and, when present, the image — so the
    model judges them together rather than in isolation."""
    content: list[dict] = [{"type": "text", "text": f"Prompt: {body.prompt}"}]
    if body.image is not None:
        mime = body.image.value.mime_type or "image/png"
        data_url = f"data:{mime};base64,{body.image.value.data}"
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": content},
    ]


async def is_safe(body: GenerateRequestBody) -> bool:
    """True when the request may be generated. False refuses it."""
    from services import llm_registry

    try:
        response = await llm_registry.call(
            process=LLM_PROCESS,
            messages=_build_messages(body),
            temperature=0.0,
            max_tokens=8,
        )
    except Exception as e:
        logger.warning("videogen prompt check: LLM call failed (%s) — refusing", e)
        return False

    verdict = (response.content or "").strip().upper()
    if verdict.startswith("SAFE"):
        return True
    if verdict.startswith("UNSAFE"):
        return False

    logger.warning(
        "videogen prompt check: unparseable verdict %r — refusing", verdict[:40]
    )
    return False
