import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import monitor

BRANDS = ["coolblue", "apple", "mediamarkt", "bol"]


def product(pid, title, variants, vendor="Wissel", handle=None):
    return {"id": pid, "title": title, "vendor": vendor, "handle": handle or f"p{pid}", "variants": variants}


def variant(vid, price, compare_at=None, title="Default Title", available=True):
    return {"id": vid, "price": price, "compare_at_price": compare_at, "title": title, "available": available}


FEED = [
    product(1, "Coolblue cadeaukaart €100", [variant(11, "93.00")]),                     # 7% -> deal
    product(2, "Apple Store 100 euro", [variant(21, "96.00")], handle="apple-store-100-euro-x"),  # 4% -> nee
    product(3, "MediaMarkt cadeaubon", [variant(31, "40.00", "45.00")]),                  # waarde < 50
    product(4, "Bol.com cadeaukaart", [variant(41, "180.00", "200.00")]),                 # 10% -> deal
    product(5, "Bol.com cadeaukaart", [variant(51, "80.00", "100.00", available=False)]), # uitverkocht
    product(6, "Zalando cadeaukaart €100", [variant(61, "70.00")]),                       # ander merk
    product(7, "Media Markt", [variant(71, "95.00", "100.00")]),                          # precies 5% -> nee
]


class ExtractTest(unittest.TestCase):
    def test_deals(self):
        listings = monitor.extract_listings(FEED, BRANDS)
        deals = {l.key for l in listings if monitor.is_deal(l, 5, 50)}
        self.assertEqual(deals, {"1:11", "4:41"})

    def test_brand_matching(self):
        self.assertEqual(monitor.match_brand("Media Markt bon", BRANDS), "mediamarkt")
        self.assertEqual(monitor.match_brand("bol.com", BRANDS), "bol")
        self.assertIsNone(monitor.match_brand("Symbolic gift", BRANDS))

    def test_parse_value(self):
        self.assertEqual(monitor.parse_value("€ 75"), 75)
        self.assertEqual(monitor.parse_value("", "apple-store-100-euro-mk42478"), 100)
        self.assertEqual(monitor.parse_value("50,50 euro"), 50.5)


class MainTest(unittest.TestCase):
    def run_main(self, feed, state_file):
        sent = []
        with mock.patch.object(monitor, "fetch_products", return_value=feed), \
             mock.patch.object(monitor, "send_ntfy", side_effect=lambda t, m, **k: sent.append(t)), \
             mock.patch.dict(os.environ, {"STATE_FILE": str(state_file)}):
            monitor.main()
        return sent

    def test_only_new_deals_notify(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            first = self.run_main(FEED, state)
            self.assertEqual(len(first), 1)
            self.assertIn("2 deals", first[0])
            self.assertEqual(self.run_main(FEED, state), [])
            extra = FEED + [product(8, "Coolblue €250", [variant(81, "225.00")])]
            self.assertEqual(self.run_main(extra, state), ["Coolblue: 10.0% korting"])
            self.assertIn("8:81", json.loads(state.read_text())["seen"])


    def test_fetch_failure_is_skipped_and_alerts_once(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            sent = []
            err = monitor.FetchError("HTTP 429")
            with mock.patch.object(monitor, "fetch_products", side_effect=err), \
                 mock.patch.object(monitor, "send_ntfy", side_effect=lambda t, m, **k: sent.append(t)), \
                 mock.patch.dict(os.environ, {"STATE_FILE": str(state)}):
                for _ in range(monitor.FAILURE_ALERT_AFTER + 2):
                    self.assertEqual(monitor.main(), 0)
            self.assertEqual(sent, ["Wissel monitor werkt niet"])
            # Eerste geslaagde run na storing: herstelmelding + gewone eerste-run melding.
            sent = self.run_main(FEED, state)
            self.assertEqual(sent[0], "Wissel monitor werkt weer")
            self.assertIn("2 deals", sent[1])
            self.assertNotIn("failures", json.loads(state.read_text()))


class FetchJsonTest(unittest.TestCase):
    def test_retries_on_429(self):
        import io
        import urllib.error
        ok = mock.MagicMock()
        ok.__enter__.return_value = io.BytesIO(b'{"products": []}')
        too_many = urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "1"}, None)
        with mock.patch("urllib.request.urlopen", side_effect=[too_many, ok]), \
             mock.patch.object(monitor.time, "sleep") as sleep:
            self.assertEqual(monitor.fetch_json("https://x.test/p.json"), {"products": []})
        sleep.assert_called_once_with(1)

    def test_gives_up_after_retries(self):
        import urllib.error
        too_many = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=too_many), \
             mock.patch.object(monitor.time, "sleep"):
            with self.assertRaises(monitor.FetchError):
                monitor.fetch_json("https://x.test/p.json")


if __name__ == "__main__":
    unittest.main()
