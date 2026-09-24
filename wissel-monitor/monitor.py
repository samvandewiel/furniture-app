#!/usr/bin/env python3
"""Houdt wissel.nl in de gaten op nieuwe cadeaubon-listings en stuurt een pushmelding.

Per merk staat het aanbod op /kopen/cadeaubonnen-met-korting/<merk>. Elke listing is een
blok als <div id="product-v1-b75-n5000-s4700-exp26-12-31" data-nominal-value="50.0" ...>:
n = nominale waarde in centen, s = verkoopprijs in centen, exp = vervaldatum.

Configuratie via omgevingsvariabelen:
  NTFY_TOPIC      ntfy.sh-topic waar meldingen heen gaan (verplicht om te versturen)
  NTFY_SERVER     standaard https://ntfy.sh
  MIN_DISCOUNT    minimale korting in procent, strikt groter dan (standaard 5)
  MIN_VALUE       minimale nominale waarde in euro (standaard 50)
  BRANDS          komma-gescheiden merken of wissel.nl-slugs (standaard coolblue,apple,mediamarkt,bol)
  STATE_FILE      pad naar het bestand met al geziene listings (standaard state.json)
  DRY_RUN=1       niets versturen, alleen printen
"""
from __future__ import annotations

import html
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

# Merknaam -> (slug op wissel.nl, weergavenaam). Onbekende namen worden als slug gebruikt.
BRAND_SLUGS = {
    "coolblue": ("coolblue", "Coolblue"),
    "apple": ("apple-gift-card-nl", "Apple"),
    "mediamarkt": ("mediamarkt", "MediaMarkt"),
    "bol": ("bol", "Bol.com"),
}

PRODUCT_RE = re.compile(r'<div\b[^>]*\bid="product-(v\d+-[^"]+)"[^>]*>', re.I)
SKU_RE = re.compile(r"-n(\d+)-s(\d+)(?:-exp(\d{2}-\d{2}-\d{2}))?")
STOCK_RE = re.compile(r"(\d+\+?)\s+op voorraad", re.I)

FAILURE_ALERT_AFTER = 6  # ~1 uur bij een run per 10 minuten
RETRY_WAITS = [10, 30, 60]  # seconden


class FetchError(RuntimeError):
    pass


@dataclass
class Listing:
    key: str
    brand: str
    url: str
    price: float
    face_value: float
    expires: str | None = None
    stock: str | None = None

    @property
    def discount(self) -> float:
        return (self.face_value - self.price) / self.face_value * 100


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "nl-NL,nl;q=0.9"}
    )
    last_err: Exception | None = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            if err.code != 429 and err.code < 500:
                raise FetchError(f"{url}: HTTP {err.code}") from err
            last_err = err
            wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
            retry_after = err.headers.get("Retry-After") if err.headers else None
            if retry_after and retry_after.isdigit():
                wait = min(int(retry_after), 120)
        except (urllib.error.URLError, TimeoutError) as err:
            last_err = err
            wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
        if attempt < len(RETRY_WAITS):
            print(f"{url}: {last_err}, opnieuw over {wait}s")
            time.sleep(wait)
    raise FetchError(f"{url}: {last_err}")


def brand_info(brand: str) -> tuple[str, str]:
    return BRAND_SLUGS.get(brand, (brand, brand.capitalize()))


def brand_url(brand: str) -> str:
    return f"{BASE_URL}/kopen/cadeaubonnen-met-korting/{brand_info(brand)[0]}"


def parse_listings(page: str, brand: str) -> list[Listing]:
    """Haalt alle listings uit de HTML van een merkpagina."""
    matches = list(PRODUCT_RE.finditer(page))
    listings: dict[str, Listing] = {}
    for i, m in enumerate(matches):
        sku = html.unescape(m.group(1))
        tag = m.group(0)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page)
        block = page[m.end():end]

        sku_m = SKU_RE.search(sku)
        nominal_attr = re.search(r'data-nominal-value="([\d.]+)"', tag)
        if sku_m:
            face, price = int(sku_m.group(1)) / 100, int(sku_m.group(2)) / 100
        elif nominal_attr:
            # Terugval als het SKU-formaat ooit verandert: waarde + kortingspercentage.
            face = float(nominal_attr.group(1))
            perc = re.search(r'data-discount-perc="(-?[\d.]+)"', tag)
            price = face * (1 - abs(float(perc.group(1))) / 100) if perc else face
        else:
            continue
        if face <= 0:
            continue
        stock = STOCK_RE.search(block)
        listings.setdefault(sku, Listing(
            key=f"{brand}:{sku}",
            brand=brand,
            url=brand_url(brand),
            price=price,
            face_value=face,
            expires=sku_m.group(3) if sku_m and sku_m.group(3) else None,
            stock=stock.group(1) if stock else None,
        ))
    return list(listings.values())


def fetch_listings(brands: list[str]) -> list[Listing]:
    listings: list[Listing] = []
    for i, brand in enumerate(brands):
        if i:
            time.sleep(2)
        found = parse_listings(fetch_html(brand_url(brand)), brand)
        best = max(found, key=lambda l: l.discount, default=None)
        print(f"{brand_info(brand)[1]}: {len(found)} listings" + (f", hoogste korting: {fmt(best).splitlines()[0]}" if best else ""))
        listings.extend(found)
    return listings


def is_deal(listing: Listing, min_discount: float, min_value: float) -> bool:
    # Afronden voorkomt dat 4,999...% door floating point als "boven 5%" telt.
    return listing.face_value >= min_value and round(listing.discount, 2) > min_discount


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


def euro(amount: float) -> str:
    return f"€{amount:.2f}".replace(".", ",").replace(",00", "")


def fmt(listing: Listing) -> str:
    extra = []
    if listing.expires:
        yy, mm, dd = listing.expires.split("-")
        extra.append(f"geldig t/m {dd}-{mm}-20{yy}")
    if listing.stock:
        extra.append(f"{listing.stock} op voorraad")
    line = (
        f"{brand_info(listing.brand)[1]} {euro(listing.face_value)} voor {euro(listing.price)} "
        f"({listing.discount:.1f}% korting)"
    )
    return line + (f"\n{', '.join(extra)}" if extra else "")


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
        listings = fetch_listings(brands)
        if not listings:
            # Alle merken tegelijk leeg is vrijwel onmogelijk: waarschijnlijk is de site veranderd.
            raise FetchError("geen enkele listing gevonden; is de opmaak van wissel.nl veranderd?")
    except FetchError as err:
        # Niet crashen: deze run overslaan en pas na een uur aan mislukte runs waarschuwen.
        failures = state.get("failures", 0) + 1
        print(f"::warning::Wissel.nl niet uit te lezen ({failures}x op rij): {err}")
        if failures == FAILURE_ALERT_AFTER:
            send_ntfy("Wissel monitor werkt niet", f"Wissel.nl is al {failures} runs op rij niet uit te lezen.\n{err}", priority=3)
        state["failures"] = failures
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
        return 0
    if state.get("failures", 0) >= FAILURE_ALERT_AFTER:
        send_ntfy("Wissel monitor werkt weer", "Wissel.nl is weer uit te lezen.", priority=2)

    deals = [l for l in listings if is_deal(l, min_discount, min_value)]
    new_deals = [d for d in deals if d.key not in seen]
    print(f"{len(listings)} listings; {len(deals)} deals; {len(new_deals)} nieuw")

    if first_run and new_deals:
        best = sorted(new_deals, key=lambda d: d.discount, reverse=True)
        body = "\n\n".join(fmt(d) for d in best[:10])
        if len(best) > 10:
            body += f"\n\n…en nog {len(best) - 10} meer"
        send_ntfy(f"Wissel monitor actief: {len(best)} deals nu", body, click=f"{BASE_URL}/kopen/cadeaubonnen-met-korting", priority=3)
    elif first_run:
        send_ntfy(
            "Wissel monitor actief",
            f"Ik volg {len(listings)} listings. Er is nu geen deal die aan je criteria voldoet; "
            "je krijgt een melding zodra er een verschijnt.",
            priority=3,
        )
    else:
        for deal in sorted(new_deals, key=lambda d: d.discount, reverse=True):
            send_ntfy(f"{brand_info(deal.brand)[1]}: {deal.discount:.1f}% korting", fmt(deal), click=deal.url)

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
