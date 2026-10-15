import time
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

from backend.dewu_bot import DewuBot, JobCancelled
from backend.config import settings
from PIL import Image, ImageDraw


PKG = "com.shizhuang.duapp"


def node(parent, text="", resource="", bounds="[20,600][120,640]", **attrs):
    return ET.SubElement(parent, "node", {
        "text": text, "resource-id": f"{PKG}:id/{resource}" if resource else "",
        "package": PKG, "bounds": bounds, **attrs,
    })


def income_xml(selected=True):
    root = ET.Element("hierarchy")
    node(root, "预计收入", "itemIncome", "[100,490][250,560]", selected=str(selected).lower())
    node(root, "最低出售价", "itemMinPrice", "[20,490][90,560]", selected=str(not selected).lower())
    node(root, "货号：112541111-3", "tvArticleNumber")
    grid = node(root, resource="skusView", bounds="[0,700][1920,980]")
    for i, (size, price) in enumerate((("39", "¥404.22"), ("40", "¥385.82"), ("41", "¥--"))):
        cell = node(grid, resource="layProperty", bounds=f"[{i * 170},790][{i * 170 + 160},880]")
        node(cell, size, "tvName", f"[{i * 170},790][{i * 170 + 150},830]")
        node(cell, price, "tvPrice", f"[{i * 170},840][{i * 170 + 150},875]")
    return ET.tostring(root, encoding="unicode")


def product_xml(bounds="[0,0][960,960]"):
    root = ET.Element("hierarchy")
    node(root, resource="ivFocusImage", bounds=bounds)
    node(root, resource="toolbarContainer", bounds="[0,42][960,119]")
    node(root, resource="itemMaskView", bounds="[0,897][960,960]")
    node(root, resource="itemRightIcon", bounds="[977,56][1903,231]")
    node(root, resource="multiDivider", bounds="[959,0][960,1080]")
    return ET.tostring(root, encoding="unicode")


class BotTests(unittest.TestCase):
    def setUp(self):
        self.bot = DewuBot()

    def test_sku_requires_exact_normalized_match(self):
        self.assertTrue(self.bot._sku_matches("112541111-3", "1125411113"))
        self.assertFalse(self.bot._sku_matches("112541111-3", "112541111-30"))
        self.assertFalse(self.bot._sku_matches("1125D1111-3", "112541111-3"))
        self.assertFalse(self.bot._sku_matches("1125", "112541111-3"))

    def test_explicit_article_aliases(self):
        root = ET.Element("hierarchy")
        node(root, "主货号：112541111-3", "tvMainNumber")
        node(root, "辅助货号：812541111-3,1125D1111-3", "tvSubNumber")
        self.assertEqual(self.bot._extract_article_numbers(ET.tostring(root, encoding="unicode")),
                         ["112541111-3", "812541111-3", "1125D1111-3"])

    def test_confirmed_alias_must_belong_to_current_product(self):
        self.bot._confirmed_articles = ["112541111-3", "1125D1111-3"]
        self.assertTrue(self.bot._verify_sku_on_sell_sheet("1125D1111-3", income_xml()))
        self.bot._d = Mock()
        self.bot._d.return_value.exists.return_value = False
        self.assertFalse(self.bot._verify_sku_on_sell_sheet("1125D1111-3",
                         income_xml().replace("112541111-3", "OTHER-001")))

    def test_decimal_and_spaced_prices(self):
        for text, expected in (("¥404.22", 404.22), ("¥ 3 1 2", 312),
                               ("￥1,299.50", 1299.50), ("¥ 4 0 4.22", 404.22)):
            with self.subTest(text=text):
                self.assertEqual(self.bot._price_from_text(text)[0], expected)

    def test_cell_prices_keep_their_sizes_and_decimals(self):
        rows = self.bot._extract_size_prices(income_xml())
        self.assertEqual([(r["size"], r["price"]) for r in rows],
                         [("39", 404.22), ("40", 385.82), ("41", None)])

    def test_minimum_sell_price_is_not_income(self):
        self.assertEqual(self.bot._extract_size_prices(income_xml(False)), [])

    def test_scrolled_income_keeps_verified_state_only_for_same_product(self):
        self.bot._income_article = "112541111-3"
        root = ET.fromstring(income_xml())
        for child in list(root):
            if child.get("resource-id", "").endswith(("/itemIncome", "/itemMinPrice")):
                root.remove(child)
        xml = ET.tostring(root, encoding="unicode")
        self.assertEqual(len(self.bot._extract_size_prices(xml)), 3)
        self.assertEqual(self.bot._extract_size_prices(xml.replace("112541111-3", "OTHER-001")), [])
        self.assertEqual(self.bot._extract_size_prices(income_xml(False)), [])

    def test_malformed_hierarchy_is_safe(self):
        self.assertEqual(self.bot._extract_size_prices("<broken"), [])
        self.assertEqual(self.bot._xml_nodes("<broken"), [])

    def test_background_detail_price_does_not_trigger_grid_nudge(self):
        xml = income_xml().replace("</hierarchy>", '<node text="¥312" bounds="[900,60][980,100]"/></hierarchy>')
        sizes, prices, _ = self.bot._size_sheet_nodes(xml)
        self.assertEqual(len(sizes), 3)
        self.assertEqual(len(prices), 3)
        self.assertTrue(all(p["y1"] == 840 for p in prices))

    def test_search_uses_observed_bar_in_landscape(self):
        root = ET.Element("hierarchy")
        node(root, "美妆", "tvTabTile", "[822,63][900,97]")
        node(root, resource="flSearchB", bounds="[1072,42][1812,119]")
        device = Mock()
        self.bot._d = device
        device.window_size.return_value = (1920, 1080)
        self.bot._page_text = Mock(return_value=ET.tostring(root, encoding="unicode"))
        edit = Mock()
        with patch.object(self.bot, "_search_input", side_effect=[None, edit, edit]):
            self.bot._tap_search_box()
        device.click.assert_called_once_with(1318, 80)
        edit.click.assert_called_once()

    def test_no_search_bar_never_blindly_clicks_category(self):
        device = Mock()
        self.bot._d = device
        device.window_size.return_value = (1920, 1080)
        self.bot._page_text = Mock(return_value="<hierarchy/>")
        with patch.object(self.bot, "_search_input", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "找不到搜索输入框"):
                self.bot._tap_search_box()
        device.click.assert_not_called()

    def test_result_index_never_reuses_last_or_discount_price(self):
        self.bot._product_result_nodes = Mock(return_value=[{"cx": 100, "cy": 600}])
        self.bot._d = Mock()
        with self.assertRaisesRegex(RuntimeError, "没有更多"):
            self.bot._open_result_at(1)
        self.bot._d.click.assert_not_called()

    def huozhi_search_xml(self, recorded_selected=True, include_all=True):
        root = ET.Element("hierarchy")
        node(root, "ARPW009-2", bounds="[100,45][550,110]",
             **{"class": "android.widget.EditText"})
        if include_all:
            node(root, "全部", bounds="[45,130][160,178]",
                 selected=str(not recorded_selected).lower())
        node(root, "已录入商品", bounds="[180,130][360,178]",
             selected=str(recorded_selected).lower())
        return ET.tostring(root, encoding="unicode")

    def configure_huozhi_device(self):
        self.bot._d = Mock()
        self.bot._d.window_size.return_value = (667, 1080)
        self.bot._sleep = Mock()

    def test_huozhi_switches_from_recorded_to_all(self):
        self.configure_huozhi_device()
        self.bot._page_text = Mock(side_effect=[
            self.huozhi_search_xml(), self.huozhi_search_xml(False),
        ])
        self.assertTrue(self.bot._select_all_huozhi_products())
        self.bot._d.click.assert_called_once_with(102, 154)

    def test_huozhi_keeps_already_selected_all(self):
        self.configure_huozhi_device()
        self.bot._page_text = Mock(return_value=self.huozhi_search_xml(False))
        self.assertTrue(self.bot._select_all_huozhi_products())
        self.bot._d.click.assert_not_called()

    def test_huozhi_does_not_search_recorded_when_all_is_missing(self):
        self.configure_huozhi_device()
        self.bot._page_text = Mock(return_value=self.huozhi_search_xml(include_all=False))
        self.assertFalse(self.bot._select_all_huozhi_products())
        self.bot._d.click.assert_not_called()

    def test_huozhi_reports_failed_filter_switch(self):
        self.configure_huozhi_device()
        self.bot._page_text = Mock(return_value=self.huozhi_search_xml())
        with patch.object(self.bot, "_wait_until", side_effect=lambda predicate, **kw: predicate()):
            self.assertFalse(self.bot._select_all_huozhi_products())

    def test_huozhi_selects_all_before_submit_and_after_results_appear(self):
        self.configure_huozhi_device()
        result = ET.fromstring(self.huozhi_search_xml(False))
        node(result, "ARPW009-2", bounds="[180,355][310,388]")
        self.bot._page_text = Mock(return_value=ET.tostring(result, encoding="unicode"))
        events = []
        edit = Mock()
        submit = Mock()
        submit.click.side_effect = lambda: events.append("submit")
        self.bot._d.side_effect = lambda **kw: (
            edit if kw.get("className") == "android.widget.EditText" else submit
        )
        self.bot._select_all_huozhi_products = Mock(
            side_effect=lambda progress: events.append("all") or True
        )
        with patch("backend.dewu_bot.time.sleep"):
            self.assertTrue(self.bot._search_huozhi_sku("ARPW009-2"))
        self.assertEqual(events, ["all", "submit", "all"])

    def test_huozhi_search_input_alone_is_not_a_product_result(self):
        self.configure_huozhi_device()
        self.bot._page_text = Mock(return_value=self.huozhi_search_xml(False))
        self.bot._select_all_huozhi_products = Mock(return_value=True)
        with patch("backend.dewu_bot.time.sleep"):
            self.assertFalse(self.bot._search_huozhi_sku("ARPW009-2"))
        self.assertEqual(self.bot._select_all_huozhi_products.call_count, 2)

    def test_huozhi_waits_for_product_without_reclicking_all(self):
        self.configure_huozhi_device()
        loading = self.huozhi_search_xml(False)
        result = ET.fromstring(loading)
        node(result, "ARPW009-2", bounds="[180,355][310,388]")
        self.bot._page_text = Mock(side_effect=[
            loading, loading, loading, ET.tostring(result, encoding="unicode"),
        ])
        self.bot._select_all_huozhi_products = Mock(return_value=True)
        with patch("backend.dewu_bot.time.sleep"):
            self.assertTrue(self.bot._search_huozhi_sku("ARPW009-2"))
        self.assertEqual(self.bot._select_all_huozhi_products.call_count, 2)

    def test_wait_is_cancellable(self):
        self.bot.request_cancel()
        with self.assertRaises(JobCancelled):
            self.bot._wait_until(lambda: False, timeout=10)

    def configure_detail(self):
        self.bot._page_text = Mock(return_value=income_xml())
        self.bot._guess_title = Mock(return_value="Test product")
        self.bot._extract_detail_sell_price = Mock(return_value=312)
        self.bot._click_sell_entry = Mock(return_value=True)
        self.bot._verify_sku_on_sell_sheet = Mock(return_value=True)
        self.bot._click_estimated_income = Mock(return_value=True)
        self.bot._collect_size_prices_with_scroll = Mock(return_value=(
            [{"size": "39", "price": 404.22, "raw": "¥404.22"}], "income.png"
        ))
        self.bot._save_screenshot = Mock(return_value="failure.png")
        self.bot._capture_product_image = Mock(return_value="product.png")

    def test_product_image_uses_main_image_bounds_without_toolbar(self):
        self.assertEqual(self.bot._product_image_bbox(product_xml(), 1920, 1080),
                         (0, 119, 959, 897))
        self.assertIsNone(self.bot._product_image_bbox("<hierarchy/>", 1920, 1080))
        self.assertIsNone(self.bot._product_image_bbox("<broken", 1920, 1080))
        self.assertIsNone(self.bot._product_image_bbox(product_xml("[2000,0][2960,960]"), 1920, 1080))

    def test_product_image_crop_is_saved_and_white_space_trimmed(self):
        bitmap = Image.new("RGB", (1920, 1080), "white")
        ImageDraw.Draw(bitmap).rectangle((100,400,850,750), fill="purple")
        self.bot._d = Mock()
        self.bot._d.screenshot.return_value = bitmap
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, "screenshot_dir", Path(directory)):
            name = self.bot._capture_product_image(product_xml(), "sku")
            self.assertIn("sku_product_", name)
            with Image.open(Path(directory) / name) as cropped:
                self.assertEqual(cropped.size, (811,411))
                self.assertEqual(cropped.getpixel((100,100)), (128,0,128))

    def test_blank_or_failed_product_photo_does_not_block_prices(self):
        self.bot._d = Mock()
        self.bot._d.screenshot.return_value = Image.new("RGB", (1920,1080), "white")
        with patch.object(self.bot, "_sleep"):
            self.assertIsNone(self.bot._capture_product_image(product_xml(), "sku"))
        self.bot._d.screenshot.side_effect = RuntimeError("screenshot offline")
        self.assertIsNone(self.bot._capture_product_image(product_xml(), "sku"))

    def test_sale_price_never_fills_missing_income(self):
        self.configure_detail()
        self.bot._click_sell_entry.return_value = False
        result = self.bot._finish_from_detail("sku", "112541111-3", None, time.time())
        self.assertFalse(result.ok)
        self.assertEqual(result.sell_price, 312)
        self.assertIsNone(result.lowest_price)
        self.assertEqual(result.prices, [])
        self.assertEqual(result.product_image, "product.png")

    def test_failed_income_selection_stops_before_collecting(self):
        self.configure_detail()
        self.bot._click_estimated_income.return_value = False
        result = self.bot._finish_from_detail("sku", "112541111-3", None, time.time())
        self.assertFalse(result.ok)
        self.assertIsNone(result.lowest_price)
        self.bot._collect_size_prices_with_scroll.assert_not_called()

    def test_price_published_before_sales_and_kept_when_sales_fail(self):
        self.configure_detail()
        calls = []
        def capture(*args, **kwargs):
            calls.append("sales")
            raise RuntimeError("offline")
        self.bot._capture_merchant_sales = Mock(side_effect=capture)
        result = self.bot._finish_from_detail(
            "sku", "112541111-3", None, time.time(),
            on_prices=lambda result: calls.append(("prices", result.product_image)),
        )
        self.assertEqual(calls, [("prices", "product.png"), "sales"])
        self.assertTrue(result.ok)
        self.assertEqual(result.lowest_price, 404.22)
        self.assertEqual(result.sales_by_size, {})
        self.assertIsNone(result.sales_source)
        self.assertTrue(result.warnings)
        self.assertEqual(result.product_image, "product.png")

    def test_mismatched_product_photo_is_never_published(self):
        self.configure_detail()
        self.bot._verify_sku_on_sell_sheet.return_value = False
        publish = Mock()
        result = self.bot._finish_from_detail(
            "sku", "OTHER-SKU", None, time.time(), expected_sku="OTHER-SKU", on_prices=publish,
        )
        self.assertIsNone(result)
        publish.assert_not_called()

    def test_merchant_sales_are_not_fabricated_trades(self):
        self.configure_detail()
        self.bot._capture_merchant_sales = Mock(return_value=(
            "sales.png", {"39": {"sales_7d": 0}}, []
        ))
        result = self.bot._finish_from_detail("sku", "112541111-3", None, time.time())
        self.assertEqual(result.sales_source, "merchant_7d")
        self.assertEqual(result.sales_by_size["39"]["sales_7d"], 0)
        self.assertEqual(result.recent_trades, [])

    def test_merchant_collection_does_not_reclick_active_market_tab(self):
        self.configure_huozhi_device()
        self.bot._page_text = Mock(return_value="<hierarchy>规格销量排序</hierarchy>")
        self.bot._extract_merchant_size_sales = Mock(return_value={"42": {"sales_7d": 133}})
        self.bot._tap_visible_node_text = Mock()
        with patch("backend.dewu_bot.time.sleep"):
            self.assertEqual(
                self.bot._collect_merchant_size_sales_with_scroll(),
                {"42": {"sales_7d": 133}},
            )
        self.bot._tap_visible_node_text.assert_not_called()

    def test_merchant_collection_reads_first_screen_before_swiping(self):
        self.configure_huozhi_device()
        self.bot._page_text = Mock(return_value="<hierarchy>规格销量排序</hierarchy>")
        self.bot._extract_merchant_size_sales = Mock(side_effect=[
            {"42": {"sales_7d": 133}}, {}, {}, {},
        ])
        with patch("backend.dewu_bot.time.sleep"):
            self.assertEqual(
                self.bot._collect_merchant_size_sales_with_scroll(),
                {"42": {"sales_7d": 133}},
            )

    def test_huozhi_entry_is_scrolled_into_view(self):
        self.configure_huozhi_device()
        root = ET.Element("hierarchy")
        node(root, "货值计算", bounds="[300,700][400,750]")
        self.bot._page_text = Mock(side_effect=[
            "<hierarchy/>", "<hierarchy/>", ET.tostring(root, encoding="unicode"),
        ])
        self.assertTrue(self.bot._open_huozhi())
        self.bot._d.swipe.assert_called_once()
        self.bot._d.click.assert_called_once_with(350, 725)

    def test_grid_collection_never_pulls_down_sheet_header(self):
        self.bot._d = Mock()
        self.bot._d.window_size.return_value = (1920, 1080)
        self.bot._page_text = Mock(return_value=income_xml())
        self.bot._save_screenshot = Mock(return_value="income.png")
        self.bot._stitch_size_grid_shots = Mock(return_value="grid.png")
        self.bot._scroll_size_sheet_to_top = Mock()
        self.bot._d.screenshot.side_effect = RuntimeError("no bitmap in unit test")
        with patch("backend.dewu_bot.time.sleep"):
            rows, _ = self.bot._collect_size_prices_with_scroll()
        self.bot._scroll_size_sheet_to_top.assert_not_called()
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
