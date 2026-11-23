from config import settings
from fyers_apiv3 import fyersModel


def get_fyers():
    """
    Create and return a FYERS client.
    """

    return fyersModel.FyersModel(
        client_id=settings.fyers.client_id,
        token=settings.fyers.access_token,
        is_async=False,
        log_path=""
    )


def fetch_history():
    """
    Fetch historical candle data from FYERS.
    """

    fyers = get_fyers()

    request = {
        "symbol": settings.trade.symbol,
        "resolution": settings.trade.resolution,
        "date_format": "1",
        "range_from": settings.trade.range_from,
        "range_to": settings.trade.range_to,
        "cont_flag": "1"
    }

    print("Fetching historical candles...")

    response = fyers.history(request)

    if "candles" not in response:
        raise Exception(f"FYERS Error: {response}")

    candles = response["candles"]

    if not candles:
        raise Exception("No candle data returned from FYERS.")

    print(f"Fetched {len(candles)} candles.")

    return candles
