import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import monitor


def item(sku, nominal, perc, stock="1"):
    # Ingekorte versie van de echte opmaak op wissel.nl.
    return f"""
    <div id="product-{sku}" data-shop--product-filter-target="product" data-nominal-value="{nominal}"
         data-discount-perc="{perc}" data-topdeal="false">
      <div><span>{perc}%</span></div>
      <div><h4> Waarde </h4><div><span>€{nominal}</span></div></div>
      <div><div> 98 dagen geldig </div><div> {stock} op voorraad </div></div>
      <form action="/winkelwagen/toevoegen" method="post">
        <input value="{sku}" type="hidden" name="product[sku]" />
      </form>
    </div>"""


COOLBLUE = "<main>" + "".join([
    item("v1-b75-n500-s475-exp26-12-31", "5.0", "-5.0"),          # te laag
    item("v1-b75-n10000-s9500-exp26-12-31", "100.0", "-5.0"),     # precies 5% -> nee
    item("v1-b75-n10000-s9300-exp27-03-01", "100.0", "-7.0", "20+"),  # deal
    item("v1-b75-n4000-s3600-exp26-12-31", "40.0", "-10.0"),      # < €50
]) + "</main>"
BOL = "<main>" + item("v1-b12-n5000-s4500-exp26-11-30", "50.0", "-10.0") + "</main>"  # deal (precies €50)
PAGES = {"coolblue": COOLBLUE, "bol": BOL, "apple": "<main>Uitverkocht</main>", "mediamarkt": "<main></main>"}


def listings_for(pages):
    return [l for brand, page in pages.items() for l in monitor.parse_listings(page, brand)]


class ParseTest(unittest.TestCase):
    def test_parse(self):
        found = monitor.parse_listings(COOLBLUE, "coolblue")
        self.assertEqual(len(found), 4)
        deal = found[2]
        self.assertEqual((deal.face_value, deal.price, deal.expires, deal.stock), (100.0, 93.0, "27-03-01", "20+"))
        self.assertEqual(deal.key, "coolblue:v1-b75-n10000-s9300-exp27-03-01")
        self.assertTrue(deal.url.endswith("/kopen/cadeaubonnen-met-korting/coolblue"))

    def test_deals(self):
        deals = {l.key for l in listings_for(PAGES) if monitor.is_deal(l, 5, 50)}
        self.assertEqual(deals, {"coolblue:v1-b75-n10000-s9300-exp27-03-01", "bol:v1-b12-n5000-s4500-exp26-11-30"})

    def test_fallback_without_sku_amounts(self):
        page = item("v2-weird", "80.0", "-8.0")
        [l] = monitor.parse_listings(page, "coolblue")
        self.assertEqual((l.face_value, round(l.price, 2)), (80.0, 73.6))

    def test_apple_slug(self):
        self.assertTrue(monitor.brand_url("apple").endswith("/apple-gift-card-nl"))

    def test_fmt(self):
        [l] = monitor.parse_listings(BOL, "bol")
        self.assertEqual(monitor.fmt(l), "Bol.com €50 voor €45 (10.0% korting)\ngeldig t/m 30-11-2026, 1 op voorraad")


class MainTest(unittest.TestCase):
    def run_main(self, pages, state_file, fetch_error=None):
        sent = []
        fetch = mock.patch.object(
            monitor, "fetch_listings",
            side_effect=fetch_error if fetch_error else None,
            return_value=None if fetch_error else listings_for(pages),
        )
        with fetch, mock.patch.object(monitor, "send_ntfy", side_effect=lambda t, m, **k: sent.append(t)), \
             mock.patch.dict(os.environ, {"STATE_FILE": str(state_file)}):
            self.assertEqual(monitor.main(), 0)
        return sent

    def test_only_new_deals_notify(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            first = self.run_main(PAGES, state)
            self.assertEqual(len(first), 1)
            self.assertIn("2 deals", first[0])
            self.assertEqual(self.run_main(PAGES, state), [])
            extra = dict(PAGES, mediamarkt=item("v1-b9-n25000-s22500-exp27-01-01", "250.0", "-10.0"))
            self.assertEqual(self.run_main(extra, state), ["MediaMarkt: 10.0% korting"])
            self.assertIn("mediamarkt:v1-b9-n25000-s22500-exp27-01-01", json.loads(state.read_text())["seen"])

    def test_empty_result_counts_as_failure(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            self.assertEqual(self.run_main({}, state), [])
            self.assertEqual(json.loads(state.read_text())["failures"], 1)

    def test_fetch_failure_is_skipped_and_alerts_once(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            sent = []
            for _ in range(monitor.FAILURE_ALERT_AFTER + 2):
                sent += self.run_main(None, state, fetch_error=monitor.FetchError("HTTP 429"))
            self.assertEqual(sent, ["Wissel monitor werkt niet"])
            sent = self.run_main(PAGES, state)
            self.assertEqual(sent[0], "Wissel monitor werkt weer")
            self.assertIn("2 deals", sent[1])
            self.assertNotIn("failures", json.loads(state.read_text()))


class FetchTest(unittest.TestCase):
    URL = "https://x.test/p"

    def test_retries_on_429(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value = io.BytesIO(b"<html></html>")
        too_many = urllib.error.HTTPError(self.URL, 429, "Too Many Requests", {"Retry-After": "1"}, None)
        with mock.patch("urllib.request.urlopen", side_effect=[too_many, ok]), \
             mock.patch.object(monitor.time, "sleep") as sleep:
            self.assertEqual(monitor.fetch_html(self.URL), "<html></html>")
        sleep.assert_called_once_with(1)

    def test_gives_up_after_retries(self):
        too_many = urllib.error.HTTPError(self.URL, 429, "Too Many Requests", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=too_many), mock.patch.object(monitor.time, "sleep"):
            with self.assertRaises(monitor.FetchError):
                monitor.fetch_html(self.URL)

    def test_404_fails_fast(self):
        missing = urllib.error.HTTPError(self.URL, 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=missing), mock.patch.object(monitor.time, "sleep") as sleep:
            with self.assertRaises(monitor.FetchError):
                monitor.fetch_html(self.URL)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
