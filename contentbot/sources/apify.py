"""Run an Apify actor synchronously and return its dataset items."""
from __future__ import annotations

import httpx

from contentbot.config import ApifyActor

APIFY_BASE = "https://api.apify.com/v2"
APIFY_MIN_CHARGE_CAP_USD = 0.50  # Apify rejects a lower maxTotalChargeUsd; maxItems still limits the real cost


class ApifyError(Exception):
    """Apify returned an error or unexpected data."""


def charge_cap(actor: ApifyActor) -> float:
    """Hard spending cap for one run: 1.5x the expected cost plus a little for the start fee."""
    return max(APIFY_MIN_CHARGE_CAP_USD, round(actor.max_results * actor.price_per_1000 / 1000 * 1.5 + 0.05, 2))


def estimate_cost(items: int, actor: ApifyActor) -> float:
    return round(items * actor.price_per_1000 / 1000, 4)


class ApifyRunner:
    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self.http = http
        self.token = token

    async def run(
        self, actor_id: str, run_input: dict, *, max_items: int, max_charge_usd: float, timeout_s: int = 280
    ) -> list[dict]:
        url = f"{APIFY_BASE}/acts/{actor_id.replace('/', '~')}/run-sync-get-dataset-items"
        response = await self.http.post(
            url,
            params={
                "timeout": timeout_s,
                "maxItems": max_items,
                "maxTotalChargeUsd": f"{max_charge_usd:.2f}",
                "format": "json",
                "clean": "true",
            },
            json=run_input,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=timeout_s + 40,
        )
        if response.status_code >= 400:
            raise ApifyError(f"Apify {actor_id} HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        if not isinstance(data, list):
            raise ApifyError(f"Apify {actor_id} returned unexpected data")
        return [item for item in data if isinstance(item, dict)]
