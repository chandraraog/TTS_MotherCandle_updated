import time
from datetime import datetime
from pathlib import Path

from telegram_alert import send_message
from fyers_client import fetch_history
from cycle_engine import (
    calculate_cycles,
    get_current_cycle,
    get_next_cycle_time,
    detect_time_price_square,
)
from config import settings

LAST_ALERT_FILE = Path("last_alert.txt")


def get_last_alert():
    if LAST_ALERT_FILE.exists():
        return LAST_ALERT_FILE.read_text().strip()
    return ""


def save_last_alert(cycle_id):
    LAST_ALERT_FILE.write_text(cycle_id)


def main():

    print("=" * 60)
    print("BankNifty 3-Min Cycle Bot")
    print("=" * 60)

    try:

        # ----------------------------------------
        # Fetch historical candles
        # ----------------------------------------
        candles = fetch_history()

        # ----------------------------------------
        # Calculate cycles
        # ----------------------------------------
        current_running, next_upcoming, bar_gap = calculate_cycles(candles)

        print(f"\nBar Gap : {bar_gap}")

        # ----------------------------------------
        # Current Cycle
        # ----------------------------------------
        current_cycle = get_current_cycle(
            candles,
            current_running
        )

        if current_cycle:

            print("\nCurrent Running Cycle")
            print("-" * 40)
            print(f"Date : {current_cycle['datetime']:%Y-%m-%d %H:%M}")
            print(f"High : {current_cycle['high']}")
            print(f"Low  : {current_cycle['low']}")

        # ----------------------------------------
        # Next Upcoming Cycle
        # ----------------------------------------
        next_cycle = get_next_cycle_time(
            candles,
            current_running,
            next_upcoming
        )

        if next_cycle:

            print("\nNext Upcoming Cycle")
            print("-" * 40)
            print(next_cycle.strftime("%Y-%m-%d %H:%M"))

        # ----------------------------------------
        # Time Price Square Analysis
        # ----------------------------------------
        tps = detect_time_price_square(
            candles=candles,
            current_running=current_running,
            bar_gap=bar_gap,
            tolerance=0.0,
            lookback_cycles=3
        )

        print("\nTime Price Square Analysis")
        print("-" * 40)

        if tps["is_tps"]:

            print(f"Status   : YES")
            print(f"Strength : {tps['strength']}/3")

            for match in tps["matches"]:
                print(
                    f"Matched Cycle -{match['cycle']} "
                    f"({match['datetime']:%Y-%m-%d %H:%M})"
                )

        else:

            print("Status : Normal Cycle")

        # ----------------------------------------
        # Telegram Alert
        # ----------------------------------------
        TEST_MODE = True

        if current_cycle and (
            TEST_MODE
            or current_cycle["datetime"].strftime("%Y-%m-%d %H:%M")
            == datetime.now().strftime("%Y-%m-%d %H:%M")
        ):

            cycle_id = current_cycle["datetime"].strftime("%Y-%m-%d %H:%M")
            last_alert = get_last_alert()

            if cycle_id == last_alert:
                print(f"\nAlert already sent for cycle : {cycle_id}")
                return

            if tps["is_tps"]:

                stars = "⭐" * tps["strength"]

                message = f"""
🚀 {stars} TIME PRICE SQUARE

📈 BANKNIFTY 3 Min Cycle Closed

Date : {current_cycle['datetime']:%Y-%m-%d %H:%M}

Current Cycle
--------------------------
High : {current_cycle['high']}
Low  : {current_cycle['low']}

Matched Previous Cycles : {tps['strength']}
"""

                for match in tps["matches"]:

                    reasons = []

                    if match["reason"]["high_match"]:
                        reasons.append("High Match")

                    if match["reason"]["low_match"]:
                        reasons.append("Low Match")

                    if match["reason"]["high_touch"]:
                        reasons.append("High Touch")

                    if match["reason"]["low_touch"]:
                        reasons.append("Low Touch")

                    message += f"""

Cycle -{match['cycle']}
Date : {match['datetime']:%Y-%m-%d %H:%M}
High : {match['high']}
Low  : {match['low']}
Reason : {', '.join(reasons)}
"""

                if tps["strength"] == 3:

                    message += """

🔥 HIGH PROBABILITY TIME PRICE SQUARE

Possible Explosive Move.

Wait for Directional Breakout Confirmation.
"""

                elif tps["strength"] == 2:

                    message += """

⚠ STRONG TIME PRICE SQUARE

Possible Big Move.

Wait for Directional Breakout Confirmation.
"""

                else:

                    message += """

⚠ TIME PRICE SQUARE

Watch for Directional Breakout.
"""

            else:

                message = f"""
📈 BANKNIFTY 3 Min Cycle Closed

Date : {current_cycle['datetime']:%Y-%m-%d %H:%M}

High : {current_cycle['high']}
Low  : {current_cycle['low']}

Normal Cycle
"""

            send_message(message)
            save_last_alert(cycle_id)

            print(f"\nTelegram alert sent for cycle : {cycle_id}")

    except Exception as ex:

        print(f"\nApplication Error: {ex}")


def run_scheduler():

    print("BankNifty Cycle Bot Started...")

    while True:

        try:
            main()

        except Exception as ex:
            print(f"Error : {ex}")

        print("\nWaiting 3 minutes...\n")

        time.sleep(180)


if __name__ == "__main__":
    run_scheduler()
