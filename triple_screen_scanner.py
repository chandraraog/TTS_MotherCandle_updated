from datetime import datetime, timedelta
import os
import time
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel
import pandas as pd

# Load environment variables from .env
load_dotenv()

CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN")

if not ACCESS_TOKEN:
  print("Warning: FYERS_ACCESS_TOKEN is empty in your .env file.")

# Initialize Fyers Model
fyers = fyersModel.FyersModel(
    client_id=CLIENT_ID, token=ACCESS_TOKEN, is_async=False, log_path=""
)


def get_historical_data(symbol, resolution="D"):
  """Fetch historical candles from Fyers API based on resolution,

  safely staying under Fyers' 366-day limit for Daily data.
  """
  to_date_obj = datetime.now()
  to_date_str = to_date_obj.strftime("%Y-%m-%d")

  if resolution in ["D", "1D"]:
    days_back = 345
  elif resolution in ["60", "120", "240"]:
    days_back = 120
  else:
    days_back = 30

  from_date_obj = to_date_obj - timedelta(days=days_back)
  from_date_str = from_date_obj.strftime("%Y-%m-%d")

  data = {
      "symbol": symbol,
      "resolution": resolution,
      "date_format": "1",
      "range_from": from_date_str,
      "range_to": to_date_str,
      "cont_flag": "1",
  }

  response = fyers.history(data=data)

  status = response.get("s")
  if status in ["success", "ok"] and response.get("candles"):
    candles = response.get("candles", [])
    df = pd.DataFrame(
        candles, columns=["epoch", "Open", "High", "Low", "Close", "Volume"]
    )
    df["Date"] = pd.to_datetime(df["epoch"], unit="s")
    return df
  else:
    # Only print error message when check fails so user can fix stocks.txt
    print(
        f"[ERROR] Failed for symbol [{symbol}] -> API Response: {response}"
    )
    return None


def evaluate_triple_screen(df):
  """Evaluates the Triple Screen variation rules on the latest candle."""
  if len(df) < 220:
    return None, f"Not enough data (Got {len(df)} bars, need 220+)"

  close = df["Close"]
  lookback_long = min(250, len(df) - 5)

  cond_long_term = close.iloc[-1] > close.iloc[-(lookback_long + 1)]
  cond_intermediate = close.iloc[-1] > close.iloc[-23]
  cond_pullback = close.iloc[-1] == close.iloc[-3:].min()

  is_buy = cond_long_term and cond_intermediate and cond_pullback
  is_sell = close.iloc[-1] > close.iloc[-2]

  return {
      "is_buy": is_buy,
      "is_sell": is_sell,
      "close": close.iloc[-1],
      "cond_long": cond_long_term,
      "cond_intermediate": cond_intermediate,
      "cond_pullback": cond_pullback,
  }, "Success"


def run_scanner(filepath="stocks.txt", resolution="D"):
  if not os.path.exists(filepath):
    print(f"File {filepath} not found. Please create it.")
    return

  with open(filepath, "r") as f:
    symbols = [line.strip() for line in f if line.strip()]

  print(
      f"\nScanning {len(symbols)} symbols from {filepath} using Timeframe:"
      f" [{resolution}]...\n"
  )

  buy_results = []
  sell_results = []

  for symbol in symbols:
    df = get_historical_data(symbol, resolution=resolution)

    # Pause briefly between requests to respect Fyers' 5 requests/second limit
    time.sleep(0.25)

    if df is None or df.empty:
      continue

    res, msg = evaluate_triple_screen(df)
    if res is None:
      continue

    # Segregate based on signal type
    if res["is_buy"]:
      buy_results.append({
          "Symbol": symbol,
          "Close": res["close"],
          "LongFilter": res["cond_long"],
          "InterFilter": res["cond_intermediate"],
          "Pullback3B": res["cond_pullback"],
      })
    elif res["is_sell"]:
      sell_results.append({
          "Symbol": symbol,
          "Close": res["close"],
          "LongFilter": res["cond_long"],
          "InterFilter": res["cond_intermediate"],
          "Pullback3B": res["cond_pullback"],
      })

  # Display Segregated Summaries
  print("\n" + "=" * 50)
  print("🟢 ACTIVE BUY SETUPS (Long Entry)")
  print("=" * 50)
  if buy_results:
    print(pd.DataFrame(buy_results).to_string(index=False))
  else:
    print("No Buy setups found.")

  print("\n" + "=" * 50)
  print("🔴 ACTIVE SELL / EXIT SIGNALS")
  print("=" * 50)
  if sell_results:
    print(pd.DataFrame(sell_results).to_string(index=False))
  else:
    print("No Sell/Exit signals found.")
  print("=" * 50)


if __name__ == "__main__":
  user_tf = input(
      "Enter timeframe/resolution (e.g., 1, 5, 15, 60, 240, D) [Default is 'D']:"
      " "
  ).strip()
  selected_tf = user_tf if user_tf else "D"

  run_scanner(filepath="stocks.txt", resolution=selected_tf)