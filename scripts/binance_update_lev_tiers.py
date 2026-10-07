"""
Refresh freqtrade/exchange/binance_leverage_tiers.json from the Binance futures API.

Requires API credentials in FREQTRADE__EXCHANGE__KEY / FREQTRADE__EXCHANGE__SECRET.
Run from the repository root: .venv/Scripts/python.exe scripts/binance_update_lev_tiers.py
"""

import json
import os
from pathlib import Path

import ccxt


key = os.environ.get("FREQTRADE__EXCHANGE__KEY")
secret = os.environ.get("FREQTRADE__EXCHANGE__SECRET")

proxy = os.environ.get("CI_WEB_PROXY")

exchange = ccxt.binance(
    {
        "apiKey": key,
        "secret": secret,
        "httpsProxy": proxy,
        "options": {"defaultType": "swap"},
    }
)
_ = exchange.load_markets()

lev_tiers = exchange.fetch_leverage_tiers()

# Assumes this is running in the root of the repository.
file = Path("freqtrade/exchange/binance_leverage_tiers.json")
json.dump(dict(sorted(lev_tiers.items())), file.open("w"), indent=2)
