from freqtrade.strategy import IStrategy
from pandas import DataFrame
from freqtrade.persistence import Trade
import logging

logger = logging.getLogger(__name__)


class DCAStrategy(IStrategy):
    """
    DCA strategy driven entirely by external TradingView webhooks.
    Entries and exits are forced via the Freqtrade REST API by dca-bot.
    This strategy just keeps trades alive until an explicit exit signal arrives.
    """

    INTERFACE_VERSION = 3

    timeframe = "5m"

    # No automatic stop loss or profit target — the bot manages all exits.
    stoploss = -0.99
    minimal_roi = {"0": 100}

    # Allow multiple entries on the same pair (pyramid DCA).
    position_adjustment_enable = True
    max_entry_position_adjustment = -1  # unlimited

    process_only_new_candles = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # All entries are forced externally — never auto-enter.
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # All exits are forced externally — never auto-exit.
        dataframe["exit_long"] = 0
        return dataframe

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        min_stake: float,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        # Position adjustments are handled via forcebuy from dca-bot.
        return None

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        # Exits are handled via forcesell from dca-bot.
        return None
