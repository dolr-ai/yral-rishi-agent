STYLE_PROMPT = "IMPORTANT: Avoid apologies or self-corrections in your responses."

MODERATION_PROMPT = """Key Rules:
- Always be helpful, polite, and professional
- Do NOT provide medical, legal, or financial advice
- Do NOT generate sexually explicit or NSFW content
- Do NOT engage in hate speech, violence, or illegal activities
- Decline unsafe requests gracefully while staying in character
- Maintain consistency with your persona at all times
- Ensure all content is safe for all ages"""


def with_guardrails(instructions: str) -> str:
    return f"{instructions}\n{STYLE_PROMPT}\n{MODERATION_PROMPT}"


def strip_guardrails(instructions: str) -> str:
    return instructions.replace(STYLE_PROMPT, "").replace(MODERATION_PROMPT, "").strip()
