"""Tests for app/models.py — Pydantic model validation."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_create_influencer_lowercases_name():
    from models import CreateInfluencerRequest
    req = CreateInfluencerRequest(
        name="ActivityUniv295",
        display_name="Test Bot",
        system_instructions="You are a helpful test bot for unit testing purposes.",
        bot_principal_id="bot-123",
    )
    assert req.name == "activityuniv295"


def test_create_influencer_rejects_short_name():
    from models import CreateInfluencerRequest
    with pytest.raises(Exception):
        CreateInfluencerRequest(
            name="ab",
            display_name="Test",
            system_instructions="You are a helpful test bot for unit testing purposes.",
            bot_principal_id="bot-123",
        )


def test_generate_prompt_accepts_concept():
    from models import GeneratePromptRequest
    req = GeneratePromptRequest(concept="a wise astrologer")
    assert req.concept == "a wise astrologer"


def test_generate_prompt_accepts_prompt_alias():
    from models import GeneratePromptRequest
    req = GeneratePromptRequest.model_validate({"prompt": "a fitness guru"})
    assert req.concept == "a fitness guru"


def test_validate_request_accepts_system_instructions_alias():
    from models import ValidateAndGenerateRequest
    req = ValidateAndGenerateRequest.model_validate({"system_instructions": "You are a coach."})
    assert req.concept == "You are a coach."


def test_send_message_defaults():
    from models import SendMessageRequest
    req = SendMessageRequest()
    assert req.message_type == "text"
    assert req.content is None
    assert req.media_urls is None
