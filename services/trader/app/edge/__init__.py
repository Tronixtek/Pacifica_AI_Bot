"""The edge bot: MA trend gate, candlestick entries, structural stops.

Built on one finding that dominates everything else measured in this project:
the instrument decides profitability, not the strategy. The same signal that
earns +0.047R on gold loses 0.27R on EURUSD, because spread costs six times
more there per unit of risk.

So this package is deliberately narrow. It trades only instruments whose
measured cost per unit of risk leaves room for a 1-3 percentage point edge to
survive, and it declines to trade anything else.
"""
