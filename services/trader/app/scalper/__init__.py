"""Fixed-cash-target scalper.

Opens a position, exits at a fixed dollar profit or a fixed dollar loss, and
repeats. Kept separate from the main strategy engine: it shares the MT5 client
and nothing else, and runs under its own magic number so the two never see
each other's positions.
"""
