import logging
import httpx
import config

logger = logging.getLogger(__name__)


async def send_message(text: str):
    webhook_url = config.GOOGLE_CHAT_WEBHOOK_URL
    if not webhook_url:
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook_url, json={"text": text})
            if response.status_code >= 400:
                logger.error(f"Google Chat webhook failed: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Google Chat webhook error: {e}")


async def notify_influencer_banned(influencer_id: str, influencer_name: str):
    await send_message(
        f"AI Influencer banned\nID: {influencer_id}\nName: {influencer_name}"
    )


async def notify_influencer_ban_failed(influencer_id: str, error: str):
    await send_message(
        f"Failed to ban AI Influencer\nID: {influencer_id}\nError: {error}"
    )


async def notify_influencer_unbanned(influencer_id: str, influencer_name: str):
    await send_message(
        f"AI Influencer unbanned\nID: {influencer_id}\nName: {influencer_name}"
    )


async def notify_influencer_unban_failed(influencer_id: str, error: str):
    await send_message(
        f"Failed to unban AI Influencer\nID: {influencer_id}\nError: {error}"
    )
