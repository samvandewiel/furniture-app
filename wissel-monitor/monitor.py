#!/usr/bin/env python3
"""Houdt wissel.nl en cardswap.nl in de gaten op nieuwe cadeaubon-listings en stuurt een pushmelding.

wissel.nl: per merk staat het aanbod op /kopen/cadeaubonnen-met-korting/<merk>. Elke listing
is een blok als <div id="product-v1-b75-n5000-s4700-exp26-12-31" ...>:
n = nominale waarde in centen, s = verkoopprijs in centen, exp = vervaldatum.

cardswap.nl: een Shopify-winkel; elke bon is een eigen product ("Apple 50 euro") in de
collectie /collections/<handle>/products.json.

Configuratie via omgevingsvariabelen:
  NTFY_TOPIC      ntfy.sh-topic waar meldingen heen gaan (verplicht om te versturen)
  NTFY_SERVER     standaard https://ntfy.sh
  MIN_DISCOUNT    minimale korting in procent, strikt groter dan (standaard 5)
  MIN_VALUE       minimale nominale waarde in euro (standaard 50)
                  Per platform overschreven door settings.json (zie workflow "Monitor instellingen").
  SETTINGS_REPO   owner/repo om settings.json live van GitHub te lezen (met GH_TOKEN); anders lokaal
  BRANDS          komma-gescheiden merken of wissel.nl-slugs (standaard coolblue,apple,mediamarkt,bol)
  SOURCES         welke sites (standaard wissel,cardswap)
  CARDSWAP_COLLECTIONS  cardswap-collecties (standaard apple,bol-com,bol-com-copy,coolblue)
  STATE_FILE      pad naar het bestand met al geziene listings (standaard state.json)
  RUN_MINUTES     blijf zo lang herhalen (standaard 0: één keer checken)
  CHECK_INTERVAL  seconden tussen checks in herhaalmodus (standaard 120)
  DRY_RUN=1       niets versturen, alleen printen
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_URL = os.environ.get("WISSEL_URL", "https://www.wissel.nl")
CARDSWAP_URL = os.environ.get("CARDSWAP_URL", "https://www.cardswap.nl")
SOURCE_NAMES = {"wissel": "Wissel", "cardswap": "Cardswap"}
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
HANDLE_VALUE_RE = re.compile(r"^[a-z0-9-]+_(\d+)_\d{12}", re.I)
VALUE_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:euro|eur)\b|€\s*(\d+(?:[.,]\d{1,2})?)", re.I)

FAILURE_ALERT_AFTER = 30  # ~1 uur bij een check per 2 minuten
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
    source: str = "wissel"

    @property
    def discount(self) -> float:
        return (self.face_value - self.price) / self.face_value * 100


def fetch_html(url: str, accept: str = "text/html") -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "nl-NL,nl;q=0.9"}
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


def log_found(source: str, label: str, found: list[Listing]) -> None:
    best = max(found, key=lambda l: l.discount, default=None)
    print(f"{SOURCE_NAMES[source]} {label}: {len(found)} listings"
          + (f", hoogste korting: {fmt(best).splitlines()[0]}" if best else ""))


def fetch_listings(brands: list[str]) -> list[Listing]:
    """Alle listings van wissel.nl voor de gegeven merken."""
    listings: list[Listing] = []
    for i, brand in enumerate(brands):
        if i:
            time.sleep(2)
        found = parse_listings(fetch_html(brand_url(brand)), brand)
        log_found("wissel", brand_info(brand)[1], found)
        listings.extend(found)
    if not listings:
        # Alle merken tegelijk leeg is vrijwel onmogelijk: waarschijnlijk is de site veranderd.
        raise FetchError("geen enkele listing gevonden; is de opmaak van wissel.nl veranderd?")
    return listings


def collection_brand(handle: str) -> str:
    """'bol-com-copy' -> 'bol'; onbekende collecties houden hun eigen naam."""
    return next((b for b in BRAND_SLUGS if b in handle.lower()), handle)


def parse_cardswap(products: list[dict], brand: str) -> list[Listing]:
    """Zet Shopify-producten van cardswap.nl om in listings."""
    listings = []
    for product in products:
        for variant in product.get("variants", []):
            if variant.get("available") is False:
                continue
            try:
                price = float(variant.get("price") or 0)
                compare_at = float(variant.get("compare_at_price") or 0)
            except ValueError:
                continue
            face = compare_at if compare_at > price else None
            if face is None:
                m = VALUE_RE.search(product.get("title", ""))
                if m:
                    face = float((m.group(1) or m.group(2)).replace(",", "."))
                else:  # handles zien eruit als apple_50_202610031200_3
                    m = HANDLE_VALUE_RE.search(product.get("handle", ""))
                    face = float(m.group(1)) if m else None
            if not face or not price:
                continue
            listings.append(Listing(
                key=f"cardswap:{variant.get('id')}",
                brand=brand,
                url=f"{CARDSWAP_URL}/products/{product.get('handle')}",
                price=price,
                face_value=face,
                source="cardswap",
            ))
    return listings


def fetch_cardswap(collections: list[str]) -> list[Listing]:
    """Alle listings uit de cardswap.nl-collecties; een lege collectie (uitverkocht) is normaal."""
    listings: dict[str, Listing] = {}
    for i, handle in enumerate(collections):
        if i:
            time.sleep(2)
        brand = collection_brand(handle)
        found: list[Listing] = []
        for page in range(1, 6):
            url = f"{CARDSWAP_URL}/collections/{handle}/products.json?limit=250&page={page}"
            try:
                products = json.loads(fetch_html(url, accept="application/json")).get("products", [])
            except json.JSONDecodeError as err:
                raise FetchError(f"{url}: geen JSON ({err})") from err
            found += parse_cardswap(products, brand)
            if len(products) < 250:
                break
        log_found("cardswap", handle, found)
        for listing in found:
            listings.setdefault(listing.key, listing)  # collecties kunnen overlappen
    return list(listings.values())


def fetch_source(source: str, brands: list[str], collections: list[str]) -> list[Listing]:
    if source == "wissel":
        return fetch_listings(brands)
    if source == "cardswap":
        return fetch_cardswap(collections)
    raise ValueError(f"onbekende bron: {source}")


SETTINGS_FILE = Path(__file__).with_name("settings.json")
DEFAULT_MIN_VALUE = float(os.environ.get("MIN_VALUE", "50"))
DEFAULT_MIN_DISCOUNT = float(os.environ.get("MIN_DISCOUNT", "5"))
_last_settings: dict = {}


def read_settings() -> dict:
    """settings.json: {"wissel": {"min_value": 50, "min_discount": 5}, "cardswap": {...}}.

    In de doorlopende run komt het bestand live van GitHub, zodat een wijziging via de
    workflow "Monitor instellingen" binnen één check meetelt. Lukt dat niet, dan geldt de
    laatst bekende versie, daarna het lokale bestand.
    """
    global _last_settings
    repo, token = os.environ.get("SETTINGS_REPO"), os.environ.get("GH_TOKEN")
    try:
        if repo and token:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/contents/wissel-monitor/settings.json?ref=master",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json",
                         "User-Agent": "wissel-monitor"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                _last_settings = json.load(resp)
        elif SETTINGS_FILE.exists():
            _last_settings = json.loads(SETTINGS_FILE.read_text())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as err:
        print(f"::warning::settings.json niet te lezen, vorige instellingen blijven gelden: {err}")
        if not _last_settings and SETTINGS_FILE.exists():
            _last_settings = json.loads(SETTINGS_FILE.read_text())
    return _last_settings


def thresholds_for(sources, settings: dict, default_value: float, default_discount: float) -> dict[str, tuple[float, float]]:
    """Per bron: (minimale waarde in euro, korting moet hoger zijn dan dit percentage)."""
    result = {}
    for source in sources:
        conf = settings.get(source, {}) if isinstance(settings.get(source), dict) else {}
        try:
            result[source] = (float(conf.get("min_value", default_value)), float(conf.get("min_discount", default_discount)))
        except (TypeError, ValueError):
            result[source] = (default_value, default_discount)
    return result


def describe_thresholds(thresholds: dict[str, tuple[float, float]]) -> str:
    return "\n".join(
        f"{SOURCE_NAMES.get(src, src)}: vanaf {euro(value)}, meer dan {discount:g}% korting"
        for src, (value, discount) in thresholds.items()
    )


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


def check_once(state_path: Path, brands: list[str], min_discount: float, min_value: float,
               sources: tuple[str, ...] = ("wissel",), collections: tuple[str, ...] = (),
               thresholds: dict[str, tuple[float, float]] | None = None) -> None:
    thresholds = thresholds or {}
    state = load_state(state_path)
    seen: dict = state.get("seen", {})
    # Oude staat (alleen wissel) had geen "sources" en "failures" als getal.
    started = set(state.get("sources", ["wissel"] if "seen" in state else []))
    failures = state.get("failures", {})
    if not isinstance(failures, dict):
        failures = {"wissel": failures}

    listings: list[Listing] = []
    ok_sources = []
    for source in sources:
        name = SOURCE_NAMES.get(source, source)
        try:
            found = fetch_source(source, brands, list(collections))
        except FetchError as err:
            # Niet crashen: deze bron overslaan en pas na een uur aan mislukte checks waarschuwen.
            failures[source] = failures.get(source, 0) + 1
            print(f"::warning::{name} niet uit te lezen ({failures[source]}x op rij): {err}")
            if failures[source] == FAILURE_ALERT_AFTER:
                send_ntfy(f"{name} monitor werkt niet", f"{name} is al {failures[source]} checks op rij niet uit te lezen.\n{err}", priority=3)
            continue
        if failures.pop(source, 0) >= FAILURE_ALERT_AFTER:
            send_ntfy(f"{name} monitor werkt weer", f"{name} is weer uit te lezen.", priority=2)
        listings += found
        ok_sources.append(source)

    def deal(listing: Listing) -> bool:
        value, discount = thresholds.get(listing.source, (min_value, min_discount))
        return is_deal(listing, discount, value)

    deals = [l for l in listings if deal(l)]
    new_deals = [d for d in deals if d.key not in seen]
    print(f"{time.strftime('%H:%M:%S')} {len(listings)} listings; {len(deals)} deals; {len(new_deals)} nieuw")

    for source in ok_sources:
        name = SOURCE_NAMES.get(source, source)
        source_new = sorted((d for d in new_deals if d.source == source), key=lambda d: d.discount, reverse=True)
        if source not in started:
            # Eerste keer voor deze bron: één samenvatting in plaats van een melding per deal.
            count = sum(1 for l in listings if l.source == source)
            if source_new:
                # Identieke bonnen (bijv. 5x "Apple €50 voor €46") als één regel tonen.
                lines: dict[str, int] = {}
                for deal in source_new:
                    lines[fmt(deal)] = lines.get(fmt(deal), 0) + 1
                parts = [(f"{n}x " if n > 1 else "") + line for line, n in lines.items()]
                body = "\n\n".join(parts[:10])
                if len(parts) > 10:
                    body += f"\n\n…en nog {len(parts) - 10} meer"
                send_ntfy(f"{name} monitor actief: {len(source_new)} deals nu", body, click=source_new[0].url, priority=3)
            else:
                send_ntfy(f"{name} monitor actief", f"Ik volg {count} listings. Er is nu geen deal die aan je criteria voldoet; "
                          "je krijgt een melding zodra er een verschijnt.", priority=3)
            started.add(source)
            continue
        for deal in source_new:
            send_ntfy(f"{name} · {brand_info(deal.brand)[1]}: {deal.discount:.1f}% korting", fmt(deal), click=deal.url)

    now = int(time.time())
    for deal in deals:
        seen.setdefault(deal.key, now)
    # Vergeet listings die al 30 dagen weg zijn, zodat het bestand klein blijft.
    current = {d.key for d in deals}
    seen = {k: v for k, v in seen.items() if k in current or now - v < 30 * 86400}
    new_state = {"seen": seen, "sources": sorted(started)}
    if failures:
        new_state["failures"] = failures
    state_path.write_text(json.dumps(new_state, indent=1, sort_keys=True))


def main() -> int:
    min_discount = float(os.environ.get("MIN_DISCOUNT", str(DEFAULT_MIN_DISCOUNT)))
    min_value = float(os.environ.get("MIN_VALUE", str(DEFAULT_MIN_VALUE)))
    brands = [b.strip().lower() for b in os.environ.get("BRANDS", "coolblue,apple,mediamarkt,bol").split(",") if b.strip()]
    state_path = Path(os.environ.get("STATE_FILE", "state.json"))
    run_seconds = float(os.environ.get("RUN_MINUTES", "0")) * 60
    interval = float(os.environ.get("CHECK_INTERVAL", "120"))
    sources = tuple(x.strip() for x in os.environ.get("SOURCES", "wissel,cardswap").split(",") if x.strip())
    collections = tuple(x.strip() for x in os.environ.get(
        "CARDSWAP_COLLECTIONS", "apple,bol-com,bol-com-copy,coolblue").split(",") if x.strip())

    # GitHub start geplande runs vaak uren te laat. Daarom blijft één run een paar uur
    # draaien en checkt hij zelf elke paar minuten.
    deadline = time.monotonic() + run_seconds
    shown = None
    while True:
        started = time.monotonic()
        try:
            thresholds = thresholds_for(sources, read_settings(), min_value, min_discount)
            if thresholds != shown:
                print("Drempels:\n" + describe_thresholds(thresholds))
                shown = thresholds
            check_once(state_path, brands, min_discount, min_value, sources, collections, thresholds)
        except Exception:
            if not run_seconds:
                raise
            # Eén mislukte check (bijv. ntfy even onbereikbaar) mag de lus niet stoppen.
            traceback.print_exc()
        wait = max(0.0, interval - (time.monotonic() - started))
        if time.monotonic() + wait >= deadline:
            return 0
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
