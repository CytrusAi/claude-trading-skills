#!/usr/bin/env python3
"""
AI-buildout value chain — taxonomy loader.

Loads value_chain.yaml (the layer -> ticker map) and exposes simple helpers.
This is the skill's unique IP; the screening machinery is reused from the
mover-screener skill by import (see ai_buildout.py).
"""

import os
from functools import lru_cache
from typing import Optional

try:
    import yaml
except ImportError as e:  # pragma: no cover
    raise SystemExit("PyYAML required: pip install pyyaml") from e

_YAML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "value_chain.yaml")


@lru_cache(maxsize=1)
def _data() -> dict:
    with open(_YAML_PATH) as f:
        return yaml.safe_load(f) or {}


def version() -> int:
    return int(_data().get("version", 0))


def updated() -> str:
    return str(_data().get("updated", ""))


def layers() -> dict:
    """Return the full layers dict, ordered as in the YAML."""
    return _data().get("layers", {})


def layer_keys() -> list[str]:
    return list(layers().keys())


def layer_label(key: str) -> str:
    return layers().get(key, {}).get("label", key)


def tickers_for(layer_key: str) -> list[str]:
    return list(layers().get(layer_key, {}).get("tickers", []))


def tells_for(layer_key: str) -> list[str]:
    return list(layers().get(layer_key, {}).get("bottleneck_tells", []))


def layer_for(ticker: str) -> Optional[str]:
    """First layer that lists this ticker (a name can appear in several)."""
    t = ticker.upper()
    for key, layer in layers().items():
        if t in [x.upper() for x in layer.get("tickers", [])]:
            return key
    return None


def all_layers_for(ticker: str) -> list[str]:
    """Every layer that lists this ticker."""
    t = ticker.upper()
    return [
        key for key, layer in layers().items() if t in [x.upper() for x in layer.get("tickers", [])]
    ]


def all_tickers() -> list[str]:
    """De-duplicated set of every ticker across all layers (stable order)."""
    seen: list[str] = []
    for layer in layers().values():
        for t in layer.get("tickers", []):
            if t not in seen:
                seen.append(t)
    return seen


def capex_proxies() -> dict:
    return _data().get("capex_proxies", {})


def all_capex_tickers() -> list[str]:
    seen: list[str] = []
    for group in capex_proxies().values():
        for t in group:
            if t not in seen:
                seen.append(t)
    return seen


def market_context() -> dict:
    return _data().get("market_context", {})


if __name__ == "__main__":
    print(f"value_chain v{version()} (updated {updated()})")
    print(f"layers: {len(layer_keys())}, unique tickers: {len(all_tickers())}")
    for k in layer_keys():
        print(f"  {k:22s} {layer_label(k):28s} {len(tickers_for(k))} names")
    print(f"capex proxies: {all_capex_tickers()}")
