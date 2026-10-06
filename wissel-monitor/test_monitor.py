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


def cardswap_product(pid, title, price, available=True, handle=None):
    return {"id": pid, "title": title, "handle": handle or f"p_{pid}",
            "variants": [{"id": pid * 10, "price": price, "compare_at_price": None, "available": available}]}


CARDSWAP = {
    "apple": [cardswap_product(1, "Apple 50 euro", "46.00"),            # 8% -> deal
              cardswap_product(2, "Apple 25 euro", "20.00")],           # < €50
    "coolblue": [cardswap_product(3, "Coolblue 100 euro", "96.00"),     # 4% -> nee
                 cardswap_product(4, "Coolblue 50 euro", "40.00", available=False)],
    "bol-com": [cardswap_product(5, "Bol.com 75 euro", "69.00")],       # 8% -> deal
    "bol-com-copy": [cardswap_product(5, "Bol.com 75 euro", "69.00")],  # zelfde product, dubbel
}


class CardswapTest(unittest.TestCase):
    def test_parse(self):
        found = monitor.parse_cardswap(CARDSWAP["apple"] + CARDSWAP["coolblue"], "apple")
        self.assertEqual([(l.key, l.face_value, l.price) for l in found],
                         [("cardswap:10", 50.0, 46.0), ("cardswap:20", 25.0, 20.0), ("cardswap:30", 100.0, 96.0)])
        self.assertEqual(found[0].url, "https://www.cardswap.nl/products/p_1")
        self.assertEqual(found[0].source, "cardswap")

    def test_value_from_handle_and_compare_at(self):
        p = cardswap_product(6, "Cadeaukaart", "45.00", handle="apple_50_202610031200_3")
        [l] = monitor.parse_cardswap([p], "apple")
        self.assertEqual(l.face_value, 50.0)
        p = cardswap_product(7, "Cadeaukaart", "45.00")
        p["variants"][0]["compare_at_price"] = "60.00"
        [l] = monitor.parse_cardswap([p], "apple")
        self.assertEqual(l.face_value, 60.0)

    def test_collection_brand(self):
        self.assertEqual(monitor.collection_brand("bol-com-copy"), "bol")
        self.assertEqual(monitor.collection_brand("coolblue"), "coolblue")
        self.assertEqual(monitor.collection_brand("zalando"), "zalando")


class MainTest(unittest.TestCase):
    COLLECTIONS = "apple,bol-com,bol-com-copy,coolblue"

    def run_main(self, pages, state_file, cardswap=None, fetch_error=None, sources="wissel"):
        sent = []

        def fake_fetch(url, accept="text/html"):
            if fetch_error:
                raise fetch_error
            if "cardswap" in url:
                handle = url.split("/collections/")[1].split("/")[0]
                return json.dumps({"products": (cardswap or {}).get(handle, [])})
            brand = next(b for b in monitor.BRAND_SLUGS if url.endswith("/" + monitor.brand_info(b)[0]))
            return pages.get(brand, "<main></main>")

        env = {"STATE_FILE": str(state_file), "SOURCES": sources, "CARDSWAP_COLLECTIONS": self.COLLECTIONS}
        with mock.patch.object(monitor, "fetch_html", side_effect=fake_fetch), \
             mock.patch.object(monitor.time, "sleep"), \
             mock.patch.object(monitor, "send_ntfy", side_effect=lambda t, m, **k: sent.append(t)), \
             mock.patch.dict(os.environ, env):
            self.assertEqual(monitor.main(), 0)
        return sent

    def test_only_new_deals_notify(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            first = self.run_main(PAGES, state)
            self.assertEqual(first, ["Wissel monitor actief: 2 deals nu"])
            self.assertEqual(self.run_main(PAGES, state), [])
            extra = dict(PAGES, mediamarkt=item("v1-b9-n25000-s22500-exp27-01-01", "250.0", "-10.0"))
            self.assertEqual(self.run_main(extra, state), ["Wissel · MediaMarkt: 10.0% korting"])
            self.assertIn("mediamarkt:v1-b9-n25000-s22500-exp27-01-01", json.loads(state.read_text())["seen"])

    def test_empty_result_counts_as_failure(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            self.assertEqual(self.run_main({}, state), [])
            self.assertEqual(json.loads(state.read_text())["failures"], {"wissel": 1})

    def test_fetch_failure_is_skipped_and_alerts_once(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            sent = []
            for _ in range(monitor.FAILURE_ALERT_AFTER + 2):
                sent += self.run_main(None, state, fetch_error=monitor.FetchError("HTTP 429"))
            self.assertEqual(sent, ["Wissel monitor werkt niet"])
            sent = self.run_main(PAGES, state)
            self.assertEqual(sent, ["Wissel monitor werkt weer", "Wissel monitor actief: 2 deals nu"])
            self.assertNotIn("failures", json.loads(state.read_text()))

    def test_cardswap_alongside_wissel(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            both = "wissel,cardswap"
            first = self.run_main(PAGES, state, CARDSWAP, sources=both)
            self.assertEqual(first, ["Wissel monitor actief: 2 deals nu", "Cardswap monitor actief: 2 deals nu"])

            self.assertEqual(self.run_main(PAGES, state, CARDSWAP, sources=both), [])
            more = dict(CARDSWAP, coolblue=CARDSWAP["coolblue"] + [cardswap_product(8, "Coolblue 50 euro", "46.00")])
            self.assertEqual(self.run_main(PAGES, state, more, sources=both), ["Cardswap · Coolblue: 8.0% korting"])

    def test_summary_groups_identical_cards(self):
        bodies = []
        cards = {"apple": [cardswap_product(i, "Apple 50 euro", "46.00") for i in range(1, 4)]}
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(monitor, "fetch_cardswap", return_value=monitor.parse_cardswap(cards["apple"], "apple")), \
             mock.patch.object(monitor, "send_ntfy", side_effect=lambda t, m, **k: bodies.append(m)):
            monitor.check_once(Path(d) / "s.json", [], 5, 50, ("cardswap",), ("apple",))
        self.assertEqual(bodies, ["3x Apple €50 voor €46 (8.0% korting)"])

    def test_empty_cardswap_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            sent = self.run_main(PAGES, state, {}, sources="wissel,cardswap")
            self.assertEqual(sent[1], "Cardswap monitor actief")
            self.assertNotIn("failures", json.loads(state.read_text()))

    def test_old_state_gets_cardswap_summary_only(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            self.run_main(PAGES, state)
            old = json.loads(state.read_text())
            del old["sources"]
            old["failures"] = 3  # oud formaat
            state.write_text(json.dumps(old))
            sent = self.run_main(PAGES, state, CARDSWAP, sources="wissel,cardswap")
            self.assertEqual(sent, ["Cardswap monitor actief: 2 deals nu"])


class LoopTest(unittest.TestCase):
    def run_loop(self, check_side_effect=None):
        clock = [0.0]
        calls = []

        def check(*a):
            calls.append(clock[0])
            clock[0] += 10  # een check duurt 10 seconden
            if check_side_effect:
                check_side_effect()

        def sleep(sec):
            clock[0] += sec

        with mock.patch.object(monitor, "check_once", side_effect=check), \
             mock.patch.object(monitor.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(monitor.time, "sleep", side_effect=sleep), \
             mock.patch.dict(os.environ, {"RUN_MINUTES": "10", "CHECK_INTERVAL": "120"}):
            self.assertEqual(monitor.main(), 0)
        return calls

    def test_checks_every_interval_until_deadline(self):
        self.assertEqual(self.run_loop(), [0, 120, 240, 360, 480])

    def test_error_does_not_stop_loop(self):
        err = mock.Mock(side_effect=[RuntimeError("ntfy weg")] + [None] * 10)
        with mock.patch("traceback.print_exc"):
            self.assertEqual(len(self.run_loop(err)), 5)

    def test_single_check_raises(self):
        with mock.patch.object(monitor, "check_once", side_effect=RuntimeError("x")), \
             mock.patch.dict(os.environ, {"RUN_MINUTES": "0"}):
            with self.assertRaises(RuntimeError):
                monitor.main()


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
