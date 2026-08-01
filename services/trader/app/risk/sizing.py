from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from app.mt5.models import MarketSpec


@dataclass(slots=True)
class SizingResult:
    """Outcome of converting a risk budget into a broker lot size."""

    ok: bool
    lots: float = 0.0
    lossPerLot: float = 0.0
    riskAmountUsd: float = 0.0
    source: str = "none"
    reason: str = ""


class PositionSizer:
    """Turns a risk budget in account currency into a tradable lot size.

    The whole job is answering one question: how many lots make the distance
    from entry to stop cost exactly the amount we are willing to risk? Getting
    it wrong is not a rounding problem - on a JPY pair the naive calculation is
    out by the exchange rate, roughly 150x.
    """

    def __init__(self, client=None) -> None:
        self.client = client

    async def size_for_risk(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        risk_amount: float,
        spec: MarketSpec | None,
    ) -> SizingResult:
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return SizingResult(False, reason="Stop distance is zero.")
        if risk_amount <= 0:
            return SizingResult(False, reason="Risk budget is zero.")
        if spec is None:
            return SizingResult(False, reason=f"No market spec available for {symbol}.")

        loss_per_lot, source = await self._loss_per_lot(
            symbol, side, entry_price, stop_loss, spec
        )
        if loss_per_lot is None or loss_per_lot <= 0:
            return SizingResult(
                False,
                reason=f"Could not determine the per-lot loss for {symbol} at this stop.",
            )

        raw_lots = risk_amount / loss_per_lot
        lots = self._quantize(raw_lots, spec)

        if lots <= 0:
            # Rounding up to the broker minimum would risk more than allowed,
            # so the trade is declined instead. At a small account balance this
            # is the common outcome for wide stops and must be stated plainly
            # rather than failing somewhere downstream.
            min_risk = spec.volumeMin * loss_per_lot
            return SizingResult(
                False,
                lossPerLot=loss_per_lot,
                source=source,
                reason=(
                    f"Risk budget {risk_amount:.2f} is below the broker minimum lot for {symbol}. "
                    f"{spec.volumeMin} lots at this stop would risk {min_risk:.2f}."
                ),
            )

        return SizingResult(
            True,
            lots=lots,
            lossPerLot=loss_per_lot,
            riskAmountUsd=round(lots * loss_per_lot, 2),
            source=source,
            reason=(
                f"{lots} lots risks {lots * loss_per_lot:.2f} at a "
                f"{stop_distance / spec.tickSize:.0f} point stop."
            ),
        )

    async def _loss_per_lot(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        spec: MarketSpec,
    ) -> tuple[float | None, str]:
        """Cost of one lot travelling from entry to stop, in account currency."""
        if self.client is not None:
            profit = await self.client.calc_profit(side, symbol, 1.0, entry_price, stop_loss)
            if profit is not None and profit != 0:
                # A stop is a loss, so this is negative; magnitude is what sizes.
                return abs(profit), "order_calc_profit"

        # Fallback for paper mode and any symbol the terminal will not price.
        # Correct only when the symbol's profit currency IS the account
        # currency, which is why it is never preferred over the terminal.
        if spec.tickSize > 0 and spec.tickValue > 0:
            ticks = abs(entry_price - stop_loss) / spec.tickSize
            return ticks * spec.tickValue, "spec_tick_value"

        return None, "unavailable"

    def _quantize(self, lots: float, spec: MarketSpec) -> float:
        """Round DOWN to the broker's lot step, never up.

        Rounding up would silently exceed the configured risk per trade.
        """
        step = Decimal(str(spec.volumeStep or 0.01))
        steps = (Decimal(str(lots)) / step).to_integral_value(rounding=ROUND_DOWN)
        quantized = steps * step

        if quantized < Decimal(str(spec.volumeMin)):
            return 0.0
        return float(min(quantized, Decimal(str(spec.volumeMax))))
