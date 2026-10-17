from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict

import pytest

from freqtrade.constants import Config
from freqtrade.exchange.exchange import Exchange
from freqtrade.resolvers.exchange_resolver import ExchangeResolver
from tests.conftest import EXMS, get_default_conf_usdt


class TestExchangeOnlineSetup(TypedDict):
    pair: str
    stake_currency: str
    use_ci_proxy: bool
    hasQuoteVolume: bool
    timeframe: str
    candle_count: int
    futures: bool
    futures_only: bool | None
    futures_pair: str | None
    candle_count_futures: int | None
    hasQuoteVolumeFutures: bool | None
    open_interest_history_days: int | None
    leverage_tiers_public: bool
    leverage_in_spot_market: bool
    trades_lookback_hours: int
    private_methods: list[str] | None
    sample_order: list[dict[str, Any]] | None
    sample_order_futures: list[dict[str, Any]] | None
    sample_my_trades: list[dict[str, Any]] | None
    skip_ws_tests: bool | None


EXCHANGE_FIXTURE_TYPE = tuple[Exchange, str, TestExchangeOnlineSetup]
EXCHANGE_WS_FIXTURE_TYPE = tuple[Exchange, str, str]

# Exchanges that should be tested online
EXCHANGES: dict[str, TestExchangeOnlineSetup] = {
    "binance": {
        "pair": "BTC/USDT",
        "stake_currency": "USDT",
        "use_ci_proxy": True,
        "hasQuoteVolume": True,
        "timeframe": "1h",
        "candle_count": 1000,
        "futures": True,
        "futures_pair": "BTC/USDT:USDT",
        "candle_count_futures": 499,
        "hasQuoteVolumeFutures": True,
        # Binance rejects "startTime" older than 30 days for open interest history.
        "open_interest_history_days": 30,
        "leverage_tiers_public": False,
        "leverage_in_spot_market": False,
        "trades_lookback_hours": 4,
        "private_methods": [
            "fapiPrivateGetPositionSideDual",
            "fapiPrivateGetMultiAssetsMargin",
            "sapi_get_spot_delist_schedule",
        ],
        "sample_order": [
            {
                "exchange_response": {
                    "symbol": "SOLUSDT",
                    "orderId": 3551312894,
                    "orderListId": -1,
                    "clientOrderId": "x-R4DD3S8297c73a11ccb9dc8f2811ba",
                    "transactTime": 1674493798550,
                    "price": "15.50000000",
                    "origQty": "1.10000000",
                    "executedQty": "0.00000000",
                    "cummulativeQuoteQty": "0.00000000",
                    "status": "NEW",
                    "timeInForce": "GTC",
                    "type": "LIMIT",
                    "side": "BUY",
                    "workingTime": 1674493798550,
                    "fills": [],
                    "selfTradePreventionMode": "NONE",
                },
                "pair": "SOL/USDT",
                "expected": {
                    "symbol": "SOL/USDT",
                    "id": "3551312894",
                    "timestamp": 1674493798550,
                    "datetime": "2023-01-23T17:09:58.550Z",
                    "price": 15.5,
                    "status": "open",
                    "side": "buy",
                    "amount": 1.1,
                },
            },
            {
                "exchange_response": {
                    "symbol": "SOLUSDT",
                    "orderId": 3551312894,
                    "orderListId": -1,
                    "clientOrderId": "x-R4DD3S8297c73a11ccb9dc8f2811ba",
                    "transactTime": 1674493798550,
                    "price": "15.50000000",
                    "origQty": "1.10000000",
                    "executedQty": "1.10000000",
                    "cummulativeQuoteQty": "17.05",
                    "status": "FILLED",
                    "timeInForce": "GTC",
                    "type": "LIMIT",
                    "side": "BUY",
                    "workingTime": 1674493798550,
                    "fills": [],
                    "selfTradePreventionMode": "NONE",
                },
                "pair": "SOL/USDT",
                "expected": {
                    "symbol": "SOL/USDT",
                    "id": "3551312894",
                    "timestamp": 1674493798550,
                    "datetime": "2023-01-23T17:09:58.550Z",
                    "price": 15.5,
                    "side": "buy",
                    "status": "closed",
                    "amount": 1.1,
                },
            },
        ],
        "sample_order_futures": [
            {
                # Futures - create order
                "exchange_response": {
                    "orderId": 1235611235,
                    "symbol": "ONDOUSDT",
                    "status": "FILLED",
                    "clientOrderId": "x-abvasdfasdfasd",
                    "price": "0.3817000",
                    "origQty": "977.4",
                    "executedQty": "977.4",
                    "cumQty": "977.4",
                    "timeInForce": "GTC",
                    "type": "LIMIT",
                    "reduceOnly": True,
                    "closePosition": False,
                    "side": "BUY",
                    "positionSide": "BOTH",
                    "stopPrice": "0.0000000",
                    "workingType": "CONTRACT_PRICE",
                    "priceProtect": False,
                    "origType": "LIMIT",
                    "priceMatch": "NONE",
                    "selfTradePreventionMode": "EXPIRE_MAKER",
                    "goodTillDate": 0,
                    "updateTime": 1784606414905,
                },
                "pair": "ONDO/USDT:USDT",
                "expected": {
                    "symbol": "ONDO/USDT:USDT",
                    "id": "1235611235",
                    "timestamp": 1784606414905,
                    "datetime": "2026-07-21T04:00:14.905Z",
                    "price": 0.3817,
                    "status": "closed",
                    "side": "buy",
                    "amount": 977.4,
                    "average": None,  # create order does not contain avgPrice ...
                },
            },
            {
                # Futures - fetch order
                "exchange_response": {
                    "orderId": 1235611235,
                    "symbol": "ONDOUSDT",
                    "status": "FILLED",
                    "clientOrderId": "x-abvasdfasdfasd",
                    "price": "0.3817000",
                    "avgPrice": "0.36360000",
                    "origQty": "977.4",
                    "executedQty": "977.4",
                    "cumQuote": "355.38264000",
                    "timeInForce": "GTC",
                    "type": "LIMIT",
                    "reduceOnly": True,
                    "closePosition": False,
                    "side": "BUY",
                    "positionSide": "BOTH",
                    "stopPrice": "0",
                    "workingType": "CONTRACT_PRICE",
                    "priceMatch": "NONE",
                    "selfTradePreventionMode": "EXPIRE_MAKER",
                    "goodTillDate": 0,
                    "priceProtect": False,
                    "origType": "LIMIT",
                    "time": 1784606414905,
                    "updateTime": 1784606414905,
                },
                "pair": "ONDO/USDT:USDT",
                "expected": {
                    "symbol": "ONDO/USDT:USDT",
                    "id": "1235611235",
                    "timestamp": 1784606414905,
                    "datetime": "2026-07-21T04:00:14.905Z",
                    "price": 0.3817,
                    "status": "closed",
                    "side": "buy",
                    "amount": 977.4,
                    "average": 0.3636,
                },
            },
        ],
    },
    "binanceus": {
        "pair": "BTC/USDT",
        "stake_currency": "USDT",
        "hasQuoteVolume": True,
        "timeframe": "1h",
        "candle_count": 1000,
        "futures": False,
        "skip_ws_tests": True,
        "sample_order": [
            {
                "exchange_response": {
                    "symbol": "SOLUSDT",
                    "orderId": 3551312894,
                    "orderListId": -1,
                    "clientOrderId": "x-R4DD3S8297c73a11ccb9dc8f2811ba",
                    "transactTime": 1674493798550,
                    "price": "15.50000000",
                    "origQty": "1.10000000",
                    "executedQty": "0.00000000",
                    "cummulativeQuoteQty": "0.00000000",
                    "status": "NEW",
                    "timeInForce": "GTC",
                    "type": "LIMIT",
                    "side": "BUY",
                    "workingTime": 1674493798550,
                    "fills": [],
                    "selfTradePreventionMode": "NONE",
                },
                "pair": "SOL/USDT",
                "expected": {
                    "symbol": "SOL/USDT",
                    "id": "3551312894",
                    "timestamp": 1674493798550,
                    "datetime": "2023-01-23T17:09:58.550Z",
                    "price": 15.5,
                    "status": "open",
                    "amount": 1.1,
                },
            }
        ],
    },
}

EXCHANGES_FUTURES = [exch for exch, params in EXCHANGES.items() if params.get("futures")]
EXCHANGES_SPOT = [exch for exch, params in EXCHANGES.items() if not params.get("futures_only")]


@pytest.fixture(scope="class")
def exchange_conf():
    config = get_default_conf_usdt((Path(__file__).parent / "testdata").resolve())
    config["exchange"]["pair_whitelist"] = []
    config["exchange"]["api_key"] = None
    config["exchange"]["secret"] = None
    config["dry_run"] = False
    config["entry_pricing"]["use_order_book"] = True
    config["exit_pricing"]["use_order_book"] = True
    return config


def set_test_proxy(config: Config, use_proxy: bool) -> Config:
    # Set proxy to test in CI.
    import os

    if use_proxy and (proxy := os.environ.get("CI_WEB_PROXY")):
        config1 = deepcopy(config)
        config1["exchange"]["ccxt_config"] = {
            "httpsProxy": proxy,
            "wsProxy": proxy,
        }
        return config1

    return config


def get_exchange(exchange_name, exchange_conf, class_mocker):
    exchange_params = EXCHANGES[exchange_name]
    exchange_conf = set_test_proxy(exchange_conf, exchange_params.get("use_ci_proxy", False))
    exchange_conf["exchange"]["name"] = exchange_name
    exchange_conf["stake_currency"] = exchange_params["stake_currency"]
    class_mocker.patch(f"{EXMS}.ft_additional_exchange_init")
    exchange = ExchangeResolver.load_exchange(
        exchange_conf, validate=True, load_leverage_tiers=True
    )

    return exchange, exchange_name, exchange_params


def get_futures_exchange(exchange_name, exchange_conf, class_mocker):
    exchange_params = EXCHANGES[exchange_name]

    if exchange_params.get("futures") is not True:
        pytest.skip(f"Exchange {exchange_name} does not support futures.")
    exchange_conf = deepcopy(exchange_conf)
    exchange_conf = set_test_proxy(exchange_conf, exchange_params.get("use_ci_proxy", False))
    exchange_conf["exchange"]["name"] = exchange_name
    exchange_conf["stake_currency"] = exchange_params["stake_currency"]
    exchange_conf["trading_mode"] = "futures"
    exchange_conf["margin_mode"] = "isolated"

    class_mocker.patch("freqtrade.exchange.binance.Binance.fill_leverage_tiers")
    class_mocker.patch(f"{EXMS}.fetch_trading_fees")
    class_mocker.patch(f"{EXMS}.ft_additional_exchange_init")
    class_mocker.patch(f"{EXMS}.load_cached_leverage_tiers", return_value=None)
    class_mocker.patch(f"{EXMS}.cache_leverage_tiers")

    exchange = ExchangeResolver.load_exchange(
        exchange_conf, validate=True, load_leverage_tiers=True
    )
    return exchange, exchange_name, exchange_params


@pytest.fixture(params=EXCHANGES_SPOT, scope="class")
def exchange(request, exchange_conf, class_mocker):
    exchange, name, exchange_params = get_exchange(request.param, exchange_conf, class_mocker)
    yield exchange, name, exchange_params
    exchange.close()


@pytest.fixture(params=EXCHANGES_FUTURES, scope="class")
def exchange_futures(request, exchange_conf, class_mocker):
    exchange, name, exchange_params = get_futures_exchange(
        request.param, exchange_conf, class_mocker
    )
    yield exchange, name, exchange_params
    exchange.close()


@pytest.fixture(params=["spot", "futures"], scope="class")
def exchange_mode(request):
    return request.param


@pytest.fixture(params=EXCHANGES, scope="class")
def exchange_ws(request, exchange_conf, exchange_mode, class_mocker):
    exchange_conf["exchange"]["enable_ws"] = True
    exchange_param = EXCHANGES[request.param]
    if exchange_param.get("skip_ws_tests"):
        pytest.skip(f"{request.param} does not support websocket tests.")
    if exchange_mode == "spot":
        exchange, name, _ = get_exchange(request.param, exchange_conf, class_mocker)
        pair = exchange_param["pair"]
    elif exchange_param.get("futures"):
        exchange, name, _ = get_futures_exchange(
            request.param, exchange_conf, class_mocker=class_mocker
        )
        pair = exchange_param["futures_pair"]
    else:
        pytest.skip("Exchange does not support futures.")

    if not exchange._exchange_ws:
        pytest.skip("Exchange does not support watch_ohlcv.")
    yield exchange, name, pair
    exchange.close()
