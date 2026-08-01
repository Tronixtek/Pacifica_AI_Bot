from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# Unambiguous FX majors used to fingerprint the broker's suffix convention.
# Every retail MT5 broker carries these, and none of them is a prefix of a
# different instrument, so the remainder after the canonical name is the
# broker suffix and nothing else.
_SUFFIX_PROBES = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF")

# A suffix is a short broker tag ("m", ".raw", "_i", "-ecn"), not another
# currency code. Capping the length keeps EURUSD from matching EURUSDT.
_MAX_SUFFIX_LEN = 5


@dataclass(slots=True)
class SymbolResolution:
    """Outcome of mapping configured symbol names onto broker symbol names."""

    resolved: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    suffix: str = ""
    detectedFrom: str = "none"

    @property
    def brokerSymbols(self) -> list[str]:
        return list(self.resolved.values())

    def describe(self) -> str:
        if not self.resolved:
            return f"No configured symbols could be matched to broker symbols. Missing: {', '.join(self.unresolved)}."
        suffix_note = f"broker suffix '{self.suffix}'" if self.suffix else "no broker suffix"
        message = f"Resolved {len(self.resolved)} symbols using {suffix_note} ({self.detectedFrom})."
        if self.unresolved:
            message += f" Unmatched: {', '.join(self.unresolved)}."
        return message


class SymbolResolver:
    """Maps canonical symbol names (EURUSD) onto broker names (EURUSDm).

    Brokers rarely expose plain instrument names. Exness appends 'm' on
    Standard accounts, others use '.raw', '_i', '-ecn', and so on. The bot is
    configured with canonical names so the same config works across brokers;
    this class does the translation against whatever the terminal actually
    offers.
    """

    def __init__(self, available: list[str], configured_suffix: str | None = None) -> None:
        self._available = list(available)
        self._by_upper = {name.upper(): name for name in self._available}
        self._configured_suffix = (configured_suffix or "").strip()

    def resolve(self, symbols: list[str]) -> SymbolResolution:
        suffix, detected_from = self._determine_suffix()
        result = SymbolResolution(suffix=suffix, detectedFrom=detected_from)

        for symbol in symbols:
            broker_name = self._match_one(symbol, suffix)
            if broker_name is None:
                result.unresolved.append(symbol)
            else:
                result.resolved[symbol] = broker_name

        return result

    def _determine_suffix(self) -> tuple[str, str]:
        # An explicitly configured suffix always wins - it lets an operator
        # override detection when a broker carries several symbol families.
        if self._configured_suffix:
            return self._configured_suffix, "configured"

        remainders: Counter[str] = Counter()
        for probe in _SUFFIX_PROBES:
            for upper_name, _ in self._by_upper.items():
                if not upper_name.startswith(probe):
                    continue
                remainder = upper_name[len(probe) :]
                if len(remainder) <= _MAX_SUFFIX_LEN:
                    remainders[remainder] += 1

        if not remainders:
            return "", "no-match"

        # The modal remainder across several majors is the broker's convention.
        suffix, hits = remainders.most_common(1)[0]
        if suffix == "":
            return "", "plain-names"
        return suffix, f"detected from {hits} major(s)"

    def _match_one(self, symbol: str, suffix: str) -> str | None:
        upper = symbol.upper()

        # The configured name may already be broker-exact.
        if upper in self._by_upper:
            return self._by_upper[upper]

        if suffix:
            suffixed = f"{upper}{suffix.upper()}"
            if suffixed in self._by_upper:
                return self._by_upper[suffixed]

        # Fall back to a per-symbol search for brokers that are inconsistent
        # across instrument classes (FX suffixed, metals or crypto not). Only
        # separator-led remainders are considered here: an alphanumeric
        # remainder is far more likely to be a longer instrument name than a
        # broker tag, and binding BTCUSD to BTCUSDT would silently trade the
        # wrong market. Reporting the symbol as unresolved is the safe failure.
        candidates = [
            name
            for upper_name, name in self._by_upper.items()
            if upper_name.startswith(upper)
            and self._is_separator_led(upper_name[len(upper) :])
        ]
        if not candidates:
            return None
        # Shortest remainder is the closest match to what was asked for.
        return min(candidates, key=len)

    @staticmethod
    def _is_separator_led(remainder: str) -> bool:
        if not remainder or len(remainder) > _MAX_SUFFIX_LEN:
            return False
        return remainder[0] in "._-#/"
