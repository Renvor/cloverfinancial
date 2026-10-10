"""Transaction cost models per asset class.

Costs are estimates. Real costs vary by broker and venue. The defaults are
deliberately conservative for a small retail account.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import AssetClass


@dataclass
class CostModel:
    commission_fixed: float = 0.0  # per order, in dollars
    commission_pct: float = 0.0  # of notional, per order (0.004 = 0.4%)
    spread_pct: float = 0.0  # half-spread paid on each side
    slippage_pct: float = 0.0  # additional adverse fill assumption per side
    min_quantity: float = 1e-6
    quantity_precision: int = 6

    def per_side_pct(self) -> float:
        return self.commission_pct + self.spread_pct + self.slippage_pct

    def side_cost(self, notional: float) -> float:
        """Total estimated cost of one fill (entry OR exit) at the given notional."""
        if notional <= 0:
            return 0.0
        return self.commission_fixed + notional * self.per_side_pct()

    def fees_only(self, notional: float) -> float:
        return self.commission_fixed + notional * self.commission_pct

    def slippage_only(self, notional: float) -> float:
        return notional * (self.spread_pct + self.slippage_pct)

    def round_quantity(self, qty: float, fractional_allowed: bool) -> float:
        """Round DOWN so the position never exceeds the permitted risk."""
        if qty <= 0:
            return 0.0
        if not fractional_allowed:
            return float(int(qty))
        factor = 10 ** self.quantity_precision
        return int(qty * factor) / factor

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "CostModel":
        base = cls()
        if data:
            for k, v in data.items():
                if hasattr(base, k) and v is not None:
                    setattr(base, k, type(getattr(base, k))(v))
        return base


def default_cost_models() -> dict[str, CostModel]:
    return {
        AssetClass.STOCK.value: CostModel(commission_fixed=0.0, commission_pct=0.0, spread_pct=0.0003, slippage_pct=0.0005, quantity_precision=4),
        AssetClass.ETF.value: CostModel(commission_fixed=0.0, commission_pct=0.0, spread_pct=0.0002, slippage_pct=0.0004, quantity_precision=4),
        AssetClass.CRYPTO.value: CostModel(commission_fixed=0.0, commission_pct=0.004, spread_pct=0.001, slippage_pct=0.001, quantity_precision=8),
    }


COST_EXPLANATIONS = {
    "commission_fixed": "Flat fee per order in dollars (many US stock brokers charge $0).",
    "commission_pct": "Fee as a fraction of the trade value per order (0.004 = 0.4%, typical for retail crypto).",
    "spread_pct": "Half the bid-ask spread as a fraction of price; you pay it on entry and again on exit.",
    "slippage_pct": "Extra adverse price movement assumed between decision and fill, per side.",
    "quantity_precision": "Decimal places allowed for fractional quantities.",
}
