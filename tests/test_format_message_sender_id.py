"""Pin the sender_id field in both _format_message helpers.

Mobile needs it for H2H bubble alignment — role is 'user' for both
participants in H2H, so sender_id is the only disambiguator. Keep both
helpers symmetric so AI and H2H wire payloads have the same shape.

Source-inspection tests (no FastAPI import) so the suite runs in any
environment without the full app deps."""

from pathlib import Path


def _read(path: str) -> str:
    here = Path(__file__).resolve().parent.parent
    return (here / path).read_text()


def test_human_chat_format_message_includes_sender_id():
    src = _read("app/routes/human_chat.py")
    # The literal kv-pair must appear in the return dict
    assert '"sender_id": msg.get("sender_id")' in src


def test_chat_format_message_includes_sender_id():
    src = _read("app/routes/chat.py")
    assert '"sender_id": msg.get("sender_id")' in src


def test_both_helpers_use_same_field_name():
    """Symmetry — if one helper renames sender_id later but the other
    doesn't, mobile parsing breaks asymmetrically. Catch the drift."""
    chat_src = _read("app/routes/chat.py")
    h2h_src = _read("app/routes/human_chat.py")
    snippet = '"sender_id": msg.get("sender_id")'
    assert (snippet in chat_src) == (snippet in h2h_src)
