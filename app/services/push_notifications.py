import logging
import httpx
import config

logger = logging.getLogger(__name__)


async def send_new_message_notification(
    user_id: str,
    influencer_name: str,
    message_content: str,
    conversation_id: str,
    influencer_id: str,
):
    if not config.METADATA_URL or not config.METADATA_AUTH_TOKEN:
        return

    preview = message_content[:100]
    if len(message_content) > 100:
        preview += "..."

    url = f"{config.METADATA_URL}/notifications/{user_id}/send"
    payload = {
        "data": {
            "title": f"New message from {influencer_name}",
            "body": preview,
            "conversation_id": conversation_id,
            "influencer_id": influencer_id,
            "type": "chat_message",
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {config.METADATA_AUTH_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code >= 400:
                logger.warning(f"Push notification failed: {response.status_code} for user {user_id}")
    except Exception as e:
        logger.warning(f"Push notification error (non-fatal): {e}")
