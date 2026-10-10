from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smarttrade.data.providers.bundled import BundledSampleProvider
from smarttrade.data.sample_generator import generate_bars
from smarttrade.models import AssetClass, Bar


def make_bars(n: int = 300, start_price: float = 100.0, drift: float = 0.0, seed: int = 1) -> list[Bar]:
    return generate_bars(start_price, 0.25, drift, 1_000_000, seed, bars=n)


def trending_bars(n: int = 300, step: float = 0.5, start: float = 100.0) -> list[Bar]:
    """A clean, steadily moving series for deterministic regime/strategy tests.

    ``step`` is the percentage move per bar (0.5 = +0.5%/bar). Bar ranges are
    proportional to price (about +/-0.3%) with a tiny repeating wobble, so
    volatility measured as a percentage of price stays roughly constant.
    """
    out = []
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = start
    for i in range(n):
        wobble = 0.0005 * ((i * 7) % 5 - 2)  # -0.1% .. +0.1%, repeating
        o = price
        c = price * (1 + step / 100 + wobble)
        h = max(o, c) * 1.003
        lo = min(o, c) * 0.997
        out.append(Bar(ts + timedelta(days=i), o, h, lo, c, 1_000_000 + (i % 7) * 10_000))
        price = c
    return out


@pytest.fixture
def bars() -> list[Bar]:
    return make_bars(400)


@pytest.fixture
def sample_sets():
    p = BundledSampleProvider()
    out = []
    for s in p.available_symbols():
        r = p.fetch(s["symbol"], AssetClass(s["asset_class"]))
        out.append((r.dataset, r.bars))
    return out


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """A FastAPI test client bound to a throw-away data directory, with no env/keys."""
    for key in list(os.environ):
        if key.startswith("SMARTTRADE_") or key.endswith("_API_KEY"):
            monkeypatch.delenv(key, raising=False)
    from smarttrade.api import services
    from smarttrade.db import Database
    from smarttrade.data.providers import build_default_registry

    db = Database(tmp_path / "test.db")
    services.set_services(services.Services(db=db, registry=build_default_registry()))
    from fastapi.testclient import TestClient
    from smarttrade.api.app import app

    with TestClient(app) as c:
        yield c
    services.set_services(None)
    db.close()
