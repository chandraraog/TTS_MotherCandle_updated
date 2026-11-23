"""
Unified Alert Dispatcher
=========================
Sends the same alert message to every configured channel.

- Telegram : always attempted if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set.
- Discord  : routed per alert-type and timeframe using env vars named
             DISCORD_WEBHOOK_<TYPE>_<RES>M, e.g.:
                 DISCORD_WEBHOOK_TPS_5M
                 DISCORD_WEBHOOK_TPS_15M
                 DISCORD_WEBHOOK_MC_5M
                 DISCORD_WEBHOOK_MC_15M
             If a Mother Candle + TPS both fire on the same candle,
             it's posted to BOTH matching webhooks (deduplicated if
             they happen to point to the same URL).
             If no type/timeframe-specific webhook is set for this
             alert, falls back to a single generic DISCORD_WEBHOOK_URL.
             If that's blank too, Discord is silently skipped (no
             error, no crash) - the bot keeps working Telegram-only.

Add more channels later by writing another `_send_xxx()` function and
registering it in `send_alert()`.
"""

import os
import requests


def _send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("[Telegram] Not configured, skipping.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        response.raise_for_status()
        print("[Telegram] Alert sent.")
        return True

    except requests.RequestException as ex:
        print(f"[Telegram] Error: {ex}")
        return False


def _resolve_discord_webhooks(resolution: str, types: list[str]) -> list[str]:
    """
    Look up DISCORD_WEBHOOK_<TYPE>_<RES>M for each type in `types`
    (e.g. ["TPS"], ["MC"], or ["MC", "TPS"] if both fired together).
    Falls back to the single generic DISCORD_WEBHOOK_URL if none of
    the specific ones are set. Returns a de-duplicated list of URLs.
    """
    urls: list[str] = []
    seen: set[str] = set()

    if resolution:
        for t in types:
            env_key = f"DISCORD_WEBHOOK_{t}_{resolution}M"
            url = os.getenv(env_key, "").strip()
            if url and url not in seen:
                urls.append(url)
                seen.add(url)

    if not urls:
        generic = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        if generic:
            urls.append(generic)

    return urls


def _send_discord(message: str, resolution: str = "", types: list[str] | None = None) -> bool:
    types = types or []
    urls = _resolve_discord_webhooks(resolution, types)

    if not urls:
        return False

    # Discord hard-limits a single message to 2000 characters.
    content = message if len(message) <= 1900 else message[:1900] + "\n... (truncated)"

    sent_any = False
    for webhook_url in urls:
        try:
            response = requests.post(webhook_url, json={"content": content}, timeout=10)
            response.raise_for_status()
            print(f"[Discord] Alert sent -> {webhook_url[:50]}...")
            sent_any = True
        except requests.RequestException as ex:
            print(f"[Discord] Error posting to {webhook_url[:50]}...: {ex}")

    return sent_any


def send_alert(message: str, resolution: str = "", types: list[str] | None = None) -> dict:
    """
    Broadcast `message` to every configured channel.

    `resolution` (e.g. "5", "15") and `types` (e.g. ["TPS"], ["MC"],
    ["MC", "TPS"]) let Discord route to the correct per-timeframe,
    per-type webhook. Omit them to just use the generic webhook.

    Returns which channels actually delivered it, e.g.
    {"telegram": True, "discord": False}
    """

    results = {
        "telegram": _send_telegram(message),
        "discord": _send_discord(message, resolution, types),
    }

    if not any(results.values()):
        print("[Alerts] No channel is configured (or all failed) - message below:\n" + message)

    return results
