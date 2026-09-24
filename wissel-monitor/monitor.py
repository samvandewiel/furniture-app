#!/usr/bin/env python3
"""Houdt wissel.nl in de gaten op nieuwe cadeaubon-listings en stuurt een pushmelding.

Wissel.nl draait op Shopify, dus alle producten staan in de openbare feed
/products.json. Elke variant (bijv. "Coolblue €100") telt als een listing.

Configuratie via omgevingsvariabelen:
  NTFY_TOPIC      ntfy.sh-topic waar meldingen heen gaan (verplicht om te versturen)
  NTFY_SERVER     standaard https://ntfy.sh
  MIN_DISCOUNT    minimale korting in procent, strikt groter dan (standaard 5)
  MIN_VALUE       minimale nominale waarde in euro (standaard 50)
  BRANDS          komma-gescheiden merken (standaard coolblue,apple,mediamarkt,bol)
  STATE_FILE      pad naar het bestand met al geziene listings (standaard state.json)
  DRY_RUN=1       niets versturen, alleen printen
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_URL = os.environ.get("WISSEL_URL", "https://www.wissel.nl")
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)

BRAND_PATTERNS = {
    "coolblue": r"\bcoolblue\b",
    "apple": r"\bapple\b",
    "mediamarkt": r"\bmedia\s?markt\b",
    "bol": r"\bbol(\.com)?\b",
}

VALUE_PATTERNS = [
    r"€\s*(\d+(?:[.,]\d{1,2})?)",
    r"(\d+(?:[.,]\d{1,2})?)\s*(?:euro|eur|€)\b",
    r"(\d+(?:[.,]\d{1,2})?)-euro",
]


@dataclass
class Listing:
    key: str
    brand: str
    title: str
    url: str
    price: float
    face_value: float

    @property
    def discount(self) -> float:
        return (self.face_value - self.price) / self.face_value * 100


class FetchError(RuntimeError):
    pass


FAILURE_ALERT_AFTER = 6  # ~1 uur bij een run per 10 minuten
RETRY_WAITS = [10, 30, 60]  # seconden; Shopify geeft 429 bij te veel verzoeken


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "nl-NL,nl;q=0.9"}
    )
    last_err: Exception | None = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            if err.code != 429 and err.code < 500:
                raise FetchError(f"{url}: HTTP {err.code}") from err
            last_err = err
            wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
            retry_after = err.headers.get("Retry-After") if err.headers else None
            if retry_after and retry_after.isdigit():
                wait = min(int(retry_after), 120)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            last_err = err
            wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
        if attempt < len(RETRY_WAITS):
            print(f"{url}: {last_err}, opnieuw over {wait}s")
            time.sleep(wait)
    raise FetchError(f"{url}: {last_err}")


def fetch_products() -> list[dict]:
    products: list[dict] = []
    page = 1
    while True:
        data = fetch_json(f"{BASE_URL}/products.json?limit=250&page={page}")
        batch = data.get("products", [])
        products.extend(batch)
        if len(batch) < 250 or page >= 40:
            return products
        page += 1
        time.sleep(2)


def match_brand(text: str, brands: list[str]) -> str | None:
    for brand in brands:
        pattern = BRAND_PATTERNS.get(brand, re.escape(brand))
        if re.search(pattern, text, re.IGNORECASE):
            return brand
    return None


def parse_value(*texts: str) -> float | None:
    for text in texts:
        if not text:
            continue
        for pattern in VALUE_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return float(m.group(1).replace(",", "."))
    return None


def to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def extract_listings(products: list[dict], brands: list[str]) -> list[Listing]:
    listings = []
    for product in products:
        haystack = " ".join(str(product.get(k) or "") for k in ("title", "vendor", "handle", "product_type"))
        brand = match_brand(haystack, brands)
        if not brand:
            continue
        for variant in product.get("variants", []):
            if variant.get("available") is False:
                continue
            price = to_float(variant.get("price"))
            if not price:
                continue
            compare_at = to_float(variant.get("compare_at_price"))
            # Nominale waarde: bij voorkeur de "van"-prijs, anders uit titel/handle.
            face = compare_at if compare_at and compare_at > price else parse_value(
                variant.get("title", ""), product.get("title", ""), product.get("handle", "")
            )
            if not face or face <= 0:
                continue
            title = product.get("title", "")
            if variant.get("title") and variant["title"] != "Default Title":
                title = f"{title} – {variant['title']}"
            listings.append(
                Listing(
                    key=f"{product.get('id')}:{variant.get('id')}",
                    brand=brand,
                    title=title,
                    url=f"{BASE_URL}/products/{product.get('handle')}?variant={variant.get('id')}",
                    price=price,
                    face_value=face,
                )
            )
    return listings


def is_deal(listing: Listing, min_discount: float, min_value: float) -> bool:
    return listing.face_value >= min_value and listing.discount > min_discount


def send_ntfy(title: str, message: str, click: str | None = None, priority: int = 4) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if os.environ.get("DRY_RUN") == "1" or not topic:
        print(f"[melding] {title}\n{message}\n")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": ["moneybag"]}
    if click:
        payload["click"] = click
    req = urllib.request.Request(
        server, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30):
        pass


def fmt(listing: Listing) -> str:
    return (
        f"{listing.title}\n€{listing.face_value:.2f} voor €{listing.price:.2f} "
        f"({listing.discount:.1f}% korting)"
    )


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def main() -> int:
    min_discount = float(os.environ.get("MIN_DISCOUNT", "5"))
    min_value = float(os.environ.get("MIN_VALUE", "50"))
    brands = [b.strip().lower() for b in os.environ.get("BRANDS", "coolblue,apple,mediamarkt,bol").split(",") if b.strip()]
    state_path = Path(os.environ.get("STATE_FILE", "state.json"))

    state = load_state(state_path)
    first_run = "seen" not in state
    seen: dict = state.get("seen", {})

    try:
        products = fetch_products()
    except FetchError as err:
        # Niet crashen: deze run overslaan en pas na een uur aan mislukte runs waarschuwen.
        failures = state.get("failures", 0) + 1
        print(f"::warning::Wissel.nl niet bereikbaar ({failures}x op rij): {err}")
        if failures == FAILURE_ALERT_AFTER:
            send_ntfy("Wissel monitor werkt niet", f"Wissel.nl is al {failures} runs op rij niet bereikbaar.\n{err}", priority=3)
        state["failures"] = failures
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
        return 0
    if state.get("failures", 0) >= FAILURE_ALERT_AFTER:
        send_ntfy("Wissel monitor werkt weer", "Wissel.nl is weer bereikbaar.", priority=2)

    listings = extract_listings(products, brands)
    deals = [l for l in listings if is_deal(l, min_discount, min_value)]
    new_deals = [d for d in deals if d.key not in seen]
    print(f"{len(listings)} listings voor {', '.join(brands)}; {len(deals)} deals; {len(new_deals)} nieuw")

    if first_run and new_deals:
        best = sorted(new_deals, key=lambda d: d.discount, reverse=True)
        body = "\n\n".join(fmt(d) for d in best[:10])
        if len(best) > 10:
            body += f"\n\n…en nog {len(best) - 10} meer"
        send_ntfy(f"Wissel monitor actief: {len(best)} deals nu", body, click=f"{BASE_URL}/kopen/cadeaubonnen-met-korting", priority=3)
    elif first_run:
        send_ntfy("Wissel monitor actief", "Er zijn nu geen deals die aan je criteria voldoen. Je krijgt een melding zodra er een verschijnt.", priority=3)
    else:
        for deal in sorted(new_deals, key=lambda d: d.discount, reverse=True):
            send_ntfy(f"{deal.brand.capitalize()}: {deal.discount:.1f}% korting", fmt(deal), click=deal.url)

    now = int(time.time())
    for deal in deals:
        seen.setdefault(deal.key, now)
    # Vergeet listings die al 30 dagen weg zijn, zodat het bestand klein blijft.
    current = {d.key for d in deals}
    seen = {k: v for k, v in seen.items() if k in current or now - v < 30 * 86400}
    state_path.write_text(json.dumps({"seen": seen}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
