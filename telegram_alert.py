from config import settings
import requests


def send_message(message):
    """
    Send a text message to Telegram.
    """

    url = (
        f"https://api.telegram.org/bot"
        f"{settings.telegram.bot_token}/sendMessage"
    )

    payload = {
        "chat_id": settings.telegram.chat_id,
        "text": message,
        "parse_mode": "HTML"
    }

    try:

        response = requests.post(
            url,
            data=payload,
            timeout=10
        )

        response.raise_for_status()

        print("Telegram message sent successfully.")

    except requests.RequestException as ex:

        print(f"Telegram Error: {ex}")
