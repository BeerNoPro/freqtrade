"""
custom_stoploss(after_fill=True): the hook the signal strategy uses to move the stop to
break-even once TP1 (a partial exit) has filled.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from freqtrade.enums import TradingMode
from freqtrade.persistence import Order, Trade
from freqtrade.resolvers import StrategyResolver
from freqtrade.strategy import stoploss_from_open
from freqtrade.util.datetime_helpers import dt_now
from tests.conftest import EXMS, get_patched_freqtradebot
from tests.strategy.strats.strategy_test_v3 import StrategyTestV3


class AfterFillStrategy(StrategyTestV3):
    use_custom_stoploss = True

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        return None


class NoAfterFillStrategy(StrategyTestV3):
    use_custom_stoploss = True

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return None


def test_resolver_detects_custom_stoploss_after_fill(default_conf):
    strategy = AfterFillStrategy(default_conf)
    StrategyResolver.validate_strategy(strategy)
    assert strategy._ft_stop_uses_after_fill is True

    strategy = NoAfterFillStrategy(default_conf)
    StrategyResolver.validate_strategy(strategy)
    assert strategy._ft_stop_uses_after_fill is False


def _futures_trade(fee) -> tuple[Trade, Order]:
    trade = Trade(
        pair="ETH/USDT:USDT",
        stake_amount=60.0,
        amount=30.0,
        amount_requested=30.0,
        open_rate=2.0,
        open_date=dt_now() - timedelta(hours=2),
        fee_open=fee.return_value,
        fee_close=fee.return_value,
        exchange="binance",
        is_open=True,
        is_short=False,
        leverage=1.0,
        trading_mode=TradingMode.FUTURES,
        stop_loss=1.8,
        stop_loss_pct=-0.1,
        initial_stop_loss=1.8,
        initial_stop_loss_pct=-0.1,
    )
    # TP1: half of the position was sold at +10%
    exit_order = Order(
        ft_order_side="sell",
        ft_pair=trade.pair,
        ft_is_open=False,
        ft_amount=15.0,
        ft_price=2.2,
        order_id="tp1",
        status="closed",
        symbol=trade.pair,
        order_type="limit",
        side="sell",
        price=2.2,
        average=2.2,
        amount=15.0,
        filled=15.0,
        remaining=0.0,
        order_date=dt_now(),
        order_filled_date=dt_now(),
    )
    return trade, exit_order


@pytest.mark.parametrize("uses_after_fill", [True, False])
def test_update_trade_after_fill_moves_stop_to_break_even(
    mocker, default_conf_usdt, fee, uses_after_fill
):
    default_conf_usdt["trading_mode"] = "futures"
    default_conf_usdt["margin_mode"] = "isolated"
    mocker.patch(f"{EXMS}.get_rate", return_value=2.2)
    freqtrade = get_patched_freqtradebot(mocker, default_conf_usdt)
    mocker.patch.object(freqtrade.exchange, "get_liquidation_price", return_value=None)

    after_fill_flags = []

    def custom_stoploss(pair, trade, current_time, current_rate, current_profit, after_fill, **kw):
        after_fill_flags.append(after_fill)
        # Stop at the entry price (break-even), expressed relative to the current rate
        return stoploss_from_open(0.0, current_profit, trade.is_short, trade.leverage)

    freqtrade.strategy.use_custom_stoploss = True
    freqtrade.strategy._ft_stop_uses_after_fill = uses_after_fill
    freqtrade.strategy.custom_stoploss = custom_stoploss
    freqtrade.strategy.order_filled = MagicMock()

    trade, exit_order = _futures_trade(fee)
    freqtrade._update_trade_after_fill(trade, exit_order, send_msg=False)

    assert freqtrade.strategy.order_filled.call_count == 1
    if uses_after_fill:
        assert after_fill_flags == [True]
        # Moved from 1.8 to break-even (current_profit includes fees, so slightly above entry)
        assert trade.open_rate <= trade.stop_loss == pytest.approx(2.0, abs=0.02)
    else:
        # Strategies without the after_fill argument are not called after a fill
        assert after_fill_flags == []
        assert trade.stop_loss == pytest.approx(1.8)
