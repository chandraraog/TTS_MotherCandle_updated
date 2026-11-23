"""
Application Configuration
"""

import os
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


# =====================================
# Credentials
# =====================================

@dataclass(frozen=True)
class FyersConfig:
    client_id: str
    access_token: str


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


# =====================================
# Trading Settings
# =====================================

@dataclass(frozen=True)
class TradeConfig:
    symbol: str
    resolution: str
    range_from: str
    range_to: str


# =====================================
# Cycle Settings
# =====================================

@dataclass(frozen=True)
class CycleConfig:
    origin_1: str
    origin_2: str


# =====================================
# Settings
# =====================================

class Settings:

    def __init__(self):

        self.fyers = FyersConfig(
            client_id=os.getenv("FYERS_CLIENT_ID", ""),
            access_token=os.getenv("FYERS_ACCESS_TOKEN", "")
        )

        self.telegram = TelegramConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", "")
        )
        # Timeframe example: 5 for 5 minutes, 15 for 15 minutes, 60 for 1 hour, etc.
        # symbol="NSE:NIFTY50-INDEX",
        self.trade = TradeConfig(
            symbol="NSE:NIFTYBANK-INDEX",
            resolution="3",  # 1, 3, 5, 15, 30, 60
            range_from="2026-07-17",
            range_to=datetime.now().strftime("%Y-%m-%d")
        )
        # origin_1="2026-05-21 13:03",
        # origin_2="2026-05-26 09:39"
        self.cycle = CycleConfig(
            origin_1="2026-07-17 14:54",
            origin_2="2026-07-22 11:30"
        )


settings = Settings()
