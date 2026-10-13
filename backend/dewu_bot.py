from __future__ import annotations

import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import uiautomator2 as u2

from .config import settings

ProgressCb = Callable[[str, str], None]


class JobCancelled(Exception):
    """用户主动终止当前查询。"""


@dataclass
class QueryResult:
    ok: bool
    query_type: str
    query: str
    title: str | None = None
    sell_price: float | None = None  # 详情页当前售价（如 498）
    lowest_price: float | None = None  # 各码预计收入中的最低有效价
    prices: list[float] = field(default_factory=list)
    size_prices: list[dict] = field(default_factory=list)  # [{size, price, raw}]
    article_number: str | None = None
    article_numbers: list[str] = field(default_factory=list)
    screenshot: str | None = None  # 尺码/出售页截图
    sales_screenshot: str | None = None  # 最近购买截图
    merchant_screenshot: str | None = None  # 商家版货值/销量截图
    sales_by_size: dict = field(default_factory=dict)  # size -> {sales_1d,sales_3d,sales_7d}
    recent_trades: list[dict] = field(default_factory=list)  # 解析到的成交记录
    sales_source: str | None = None
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    elapsed_ms: int = 0
    product_image: str | None = None  # 商品详情页主图裁切

    def to_dict(self) -> dict:
        return asdict(self)


class DewuBot:
    """半自动操作雷电模拟器中的得物 App，按货号/图片查价。"""

    # 兼容「¥499」「¥ 4 9 9」「¥396.58」
    PRICE_FLEX_RE = re.compile(
        r"[¥￥]\s*((?:\d\s+)+\d(?:\.\d+)?|\d+(?:,\d{3})*(?:\.\d+)?)"
    )
    SIZE_RE = re.compile(
        r"^(\d{2,3}(?:\.\d)?|[XSML]{1,4}|XXS|XXL|XXXL|XXXXL|均码|ONE SIZE)$",
        re.I,
    )
    NODE_RE = re.compile(r"<node\b([^>]*)/?>")
    ATTR_RE = re.compile(r'(\S+)="([^"]*)"')
    CLOTHING_SIZES = frozenset(
        {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "XXXXL", "均码", "ONE SIZE"}
    )

    def __init__(self) -> None:
        self._d: u2.Device | None = None
        self._cancel = threading.Event()
        self._job_running = threading.Event()
        self._confirmed_articles: list[str] = []
        self._income_article: str | None = None

    def request_cancel(self) -> bool:
        """请求终止当前查询；返回当时是否有任务在跑。"""
        running = self._job_running.is_set()
        self._cancel.set()
        return running

    def _begin_job(self) -> None:
        self._cancel.clear()
        self._job_running.set()
        self._confirmed_articles = []
        self._income_article = None

    def _end_job(self) -> None:
        self._job_running.clear()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled("用户已终止查询")

    def _sleep(self, seconds: float) -> None:
        """可中断 sleep。"""
        end = time.time() + max(0.0, seconds)
        while True:
            self._check_cancel()
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.25, left))

    def _wait_until(self, predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            self._check_cancel()
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            self._sleep(0.15)

    @staticmethod
    def _xml_nodes(xml: str) -> list[dict]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        nodes = []
        for node in root.iter("node"):
            attrs = dict(node.attrib)
            match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", attrs.get("bounds", ""))
            if not match:
                continue
            x1, y1, x2, y2 = map(int, match.groups())
            if x2 <= x1 or y2 <= y1:
                continue
            attrs.update(x1=x1, y1=y1, x2=x2, y2=y2, cx=(x1 + x2) // 2, cy=(y1 + y2) // 2)
            nodes.append(attrs)
        return nodes

    def _search_input(self):
        d = self.d
        selector = d(resourceId="com.shizhuang.duapp:id/etSearch")
        if selector.exists(timeout=0):
            return selector
        w, h = d.window_size()
        inputs = [
            n for n in self._xml_nodes(self._page_text())
            if n.get("class") == "android.widget.EditText"
        ]
        for index, node in enumerate(inputs):
            if (
                node.get("class") == "android.widget.EditText"
                and 0 <= node["cy"] < h * 0.25
                and 0 <= node["cx"] < w
                and node.get("package") == "com.shizhuang.duapp"
            ):
                return d(className=node["class"], instance=index)
        return None

    def _progress(
        self, progress: ProgressCb | None, stage: str, message: str
    ) -> None:
        self._check_cancel()
        if progress:
            progress(stage, message)

    def _ensure_adb_env(self) -> None:
        adb = Path(settings.adb_path)
        if adb.exists():
            os.environ["ANDROID_ADB_SERVER_PORT"] = os.environ.get(
                "ANDROID_ADB_SERVER_PORT", "5037"
            )
            adb_dir = str(adb.parent)
            path = os.environ.get("PATH", "")
            if adb_dir not in path:
                os.environ["PATH"] = adb_dir + os.pathsep + path

    def connect(self) -> None:
        self._ensure_adb_env()
        if self._d is not None:
            try:
                _ = self._d.window_size()
                return
            except Exception:
                self._d = None
        serial = settings.device_serial or None
        self._d = u2.connect(serial)

    @property
    def d(self) -> u2.Device:
        if self._d is None:
            self.connect()
        assert self._d is not None
        return self._d

    def _open_dewu(self) -> None:
        packages = ["com.shizhuang.duapp", "com.dewu"]
        current = self.d.app_current()
        pkg = current.get("package", "")
        if pkg in packages:
            return
        for p in packages:
            try:
                self.d.app_start(p)
                time.sleep(2.5)
                if self.d.app_current().get("package") == p:
                    return
            except Exception:
                continue
        raise RuntimeError("无法启动得物 App，请确认模拟器内已安装并登录")

    def _go_home(self, progress: ProgressCb | None = None) -> None:
        """回到得物首页。覆盖详情 / 识图 / 相册 / 出售层 / 商家货值，每次新查询前必须调用。"""
        self._progress(progress, "home", "返回得物首页…")
        d = self.d
        self._close_sell_sheet()
        self._dismiss_camera_request_dialog(progress, aggressive=False)

        # 商家货值浮层 / 商家「我」会让 MainActivity 误判成首页
        try:
            if self._is_huozhi_overlay() or self._is_seller_me_page():
                self._progress(progress, "home", "退出商家货值页…")
                self._leave_huozhi_flow()
                self._ensure_buyer_mode(progress)
        except Exception:
            pass

        def current_act() -> str:
            return d.app_current().get("activity") or ""

        def on_photo_or_search_flow(act: str, xml: str) -> bool:
            if any(
                k in act
                for k in (
                    "GraphSearch",
                    "PSResult",
                    "TotalPublish",
                    "PublishProcess",
                    "photo2",
                )
            ):
                return True
            if any(
                k in xml
                for k in (
                    "拍照搜索",
                    "我的相册",
                    "最近图搜",
                    "对准要识别",
                    "对准商品",
                )
            ):
                return True
            return False

        def on_home_feed() -> bool:
            act = current_act()
            if any(
                k in act
                for k in (
                    "ProductDetail",
                    "product_detail",
                    "GraphSearch",
                    "PSResult",
                    "Publish",
                    "photo2",
                )
            ):
                return False
            xml = self._page_text()
            if on_photo_or_search_flow(act, xml):
                return False
            # 商家货值浮层 / 商家主页不算买家首页
            if self._is_huozhi_overlay(xml):
                return False
            if self._is_seller_me_page(xml):
                return False
            if self._is_buyer_me_page(xml):
                return False
            # 已在真正首页根页还按返回会提示退出 App；买家/商家『我』页已在上面排除。
            if "再按一次返回键退出" in xml:
                return True
            if "我要出售" in xml and "货值计算" in xml:
                return False
            if any(k in xml for k in ("预计收入", "最低出售价", "去出价", "出售商品")):
                return False
            if "立即购买" in xml and "还价" in xml:
                return False
            # 底部 Tab + 顶部信息流 / 分类 ≈ 首页
            has_bottom = "得物" in xml and "购买" in xml and (
                "探索" in xml or "我" in xml
            )
            has_feed = any(
                k in xml
                for k in ("推荐", "关注", "灵感", "美妆", "直播", "新奇", "穿搭")
            )
            has_search = (
                "etSearch" in xml
                or "bgSearchView" in xml
                or d(resourceId="com.shizhuang.duapp:id/etSearch").exists(
                    timeout=0.15
                )
            )
            if has_bottom and (has_feed or has_search):
                return True
            if any(
                k in act
                for k in ("HomeActivity", "TabActivity", "DuMain")
            ):
                if on_photo_or_search_flow(act, xml):
                    return False
                if has_feed or has_search:
                    return True
                return False
            # 注意：SearchMainActivity 也含 MainActivity 子串，必须排除
            if "MainActivity" in act and "Search" not in act and "Product" not in act:
                if on_photo_or_search_flow(act, xml):
                    return False
                if has_feed or has_search:
                    return True
                return False
            return False

        def leave_photo_search() -> None:
            """从拍照搜索 / 相册展开层退出。"""
            # 展开相册时先收起，避免返回键只收起不退出
            if d(text="收起").click_exists(timeout=0.4):
                time.sleep(0.35)
            if d(resourceId="com.shizhuang.duapp:id/tvToggleButton").exists(
                timeout=0.2
            ):
                try:
                    label = (
                        d(resourceId="com.shizhuang.duapp:id/tvToggleButton").get_text()
                        or ""
                    ).strip()
                except Exception:
                    label = ""
                if label == "收起":
                    d(resourceId="com.shizhuang.duapp:id/tvToggleButton").click()
                    time.sleep(0.35)

            if d(description="转到上一层级").click_exists(timeout=0.5):
                time.sleep(0.55)
                return
            if d(descriptionContains="转到上一层").click_exists(timeout=0.3):
                time.sleep(0.55)
                return
            if d(resourceId="com.shizhuang.duapp:id/homeAsUpBtn").click_exists(
                timeout=0.4
            ):
                time.sleep(0.55)
                return
            d.press("back")
            time.sleep(0.55)

        if on_home_feed():
            self._progress(progress, "home", "已在首页，继续查询")
            return

        # 多层退出：出售 → 货值 → 详情 → 识图结果 → 拍照搜索/相册 → 普通搜索
        # 已经在买家『我』页时，直接点首页 tab，避免继续按返回触发退出提示。
        if self._is_buyer_me_page() and self._tap_buyer_home_tab(progress):
            if on_home_feed():
                self._progress(progress, "home", "已回到首页")
                return

        for i in range(12):
            if on_home_feed():
                self._progress(progress, "home", "已回到首页")
                return

            xml = self._page_text()
            act = current_act()
            self._dismiss_camera_request_dialog(progress, aggressive=False)

            if "预计收入" in xml or "最低出售价" in xml or "去出价" in xml:
                self._close_sell_sheet()
                continue

            if self._is_huozhi_overlay(xml):
                self._progress(progress, "home", f"关闭货值计算…({i + 1})")
                self._press_page_back()
                continue

            if self._is_seller_me_page(xml):
                self._progress(progress, "home", "切回买家版…")
                self._ensure_buyer_mode(progress)
                self._tap_buyer_home_tab(progress)
                # 切不掉就别空转：点购买/得物后继续判定
                if self._is_seller_me_page() and not self._is_huozhi_overlay():
                    self._tap_buyer_home_tab(progress)
                    # 仍像商家页则强制回退一次后结束本轮依赖兜底
                    if self._is_seller_me_page() and i >= 2:
                        break
                continue

            if on_photo_or_search_flow(act, xml):
                self._progress(progress, "home", f"退出识图/相册页…({i + 1})")
                leave_photo_search()
                continue

            if (
                "ProductDetail" in act
                or "product_detail" in act
                or ("立即购买" in xml and "还价" in xml)
            ):
                if d(
                    resourceId="com.shizhuang.duapp:id/homeAsUpBtn"
                ).click_exists(timeout=0.5):
                    time.sleep(0.7)
                else:
                    self._press_page_back()
                continue

            # 货号搜索页等
            if "Search" in act or (
                "搜索" in xml and "推荐" not in xml and "关注" not in xml
            ):
                self._press_page_back()
                continue

            # 未知页：若已出现退出提示则停止；否则返回一层
            if "再按一次返回键退出" in xml:
                self._tap_buyer_home_tab(progress)
                break
            self._press_page_back()

        # 点底部买家首页 tab 兜底（新版首页常显示为『购买』）。
        self._tap_buyer_home_tab(progress)

        if on_home_feed():
            self._progress(progress, "home", "已回到首页")
            return

        self._press_page_back()
        self._tap_buyer_home_tab(progress)

        # 仍停在识图页则再退两层
        for _ in range(3):
            act = current_act()
            xml = self._page_text()
            if on_home_feed():
                break
            if self._is_huozhi_overlay(xml):
                self._press_page_back()
            elif on_photo_or_search_flow(act, xml):
                leave_photo_search()
            else:
                self._press_page_back()

        if on_home_feed():
            self._progress(progress, "home", "已回到首页")
        else:
            self._progress(progress, "home", "已尝试回到首页，开始查询")

    def _tap_search_box(self) -> None:
        """进入搜索页并聚焦货号输入框，禁止乱点分类/历史（如美妆）。"""
        d = self.d
        et = self._search_input()
        if et is not None:
            et.click()
            return
        w, h = d.window_size()
        ids = ("flSearchB", "collapseSearchArea", "searchBar", "searchBox", "bgSearchView")
        nodes = self._xml_nodes(self._page_text())
        candidates = [
            n for n in nodes
            if n.get("resource-id", "").rsplit("/", 1)[-1] in ids
            and n.get("package") == "com.shizhuang.duapp"
            and 0 <= n["cy"] < h * 0.25 and 0 <= n["cx"] < w
        ]
        candidates.sort(key=lambda n: ids.index(n["resource-id"].rsplit("/", 1)[-1]))
        if not candidates:
            candidates = [
                n for n in nodes
                if (n.get("text") == "搜索" or n.get("content-desc") == "搜索")
                and n.get("package") == "com.shizhuang.duapp"
                and 0 <= n["cy"] < h * 0.25 and 0 <= n["cx"] < w
            ]
        for node in candidates[:2]:
            # The right edge of the search bar contains camera/submit buttons.
            d.click(node["x1"] + (node["x2"] - node["x1"]) // 3, node["cy"])
            if self._wait_until(lambda: self._search_input() is not None, timeout=5):
                self._search_input().click()
                return
        raise RuntimeError("找不到搜索输入框，已停止以免误点美妆等分类")

    def _read_search_input(self) -> str:
        et = self._search_input()
        if et is None:
            return ""
        try:
            return (et.get_text() or "").strip()
        except Exception:
            return ""

    def _input_text(self, text: str) -> None:
        """向搜索框写入货号，并校验内容。"""
        d = self.d
        et = self._search_input()
        if et is None:
            raise RuntimeError("搜索框不存在，无法输入货号")

        et.click()
        time.sleep(0.25)
        try:
            et.set_text(text)
            time.sleep(0.35)
        except Exception:
            pass

        got = self._read_search_input()
        if self._sku_matches(text, got):
            return

        try:
            try:
                et.set_text("")
            except Exception:
                pass
            d.clipboard = text
            time.sleep(0.1)
            d.shell("input keyevent 279")
            time.sleep(0.4)
        except Exception:
            pass

        got = self._read_search_input()
        if self._sku_matches(text, got):
            return

        try:
            d.set_fastinput_ime(True)
        except Exception:
            pass
        try:
            et.click()
            d.send_keys(text, clear=False)
            time.sleep(0.35)
        except Exception:
            pass

        got = self._read_search_input()
        if not self._sku_matches(text, got):
            raise RuntimeError(
                f"货号输入校验失败：期望「{text}」，实际「{got or '空'}」"
            )

    def _submit_search(self) -> None:
        d = self.d
        if d(resourceId="com.shizhuang.duapp:id/tvSearch").click_exists(timeout=0.2):
            return
        if d(text="搜索").click_exists(timeout=0.2):
            return
        d.press("enter")

    @staticmethod
    def _parse_price_token(raw: str) -> float | None:
        s = re.sub(r"[\s,]", "", raw or "")
        if not s or s in ("--", "—", "-"):
            return None
        try:
            n = float(s)
        except ValueError:
            return None
        if 1 <= n <= 500000:
            if abs(n - round(n)) < 1e-9:
                return float(int(round(n)))
            return round(n, 2)
        return None

    def _extract_prices(self, text: str) -> list[float]:
        values: list[float] = []
        for m in self.PRICE_FLEX_RE.finditer(text or ""):
            n = self._parse_price_token(m.group(1))
            if n is not None:
                values.append(n)
        seen: set[float] = set()
        out: list[float] = []
        for v in values:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _page_text(self) -> str:
        try:
            return self.d.dump_hierarchy()
        except Exception:
            return ""

    def _guess_title(self, xml: str) -> str | None:
        def clean(t: str) -> str:
            t = (t or "").replace("&#10;", " ").replace("\n", " ")
            t = re.sub(r"\s+", " ", t).strip()
            return t

        # 出售弹层标题
        m = re.search(r'resource-id="[^"]*tvPropName"[^>]*text="([^"]+)"', xml)
        if m:
            return clean(m.group(1)) or None
        m = re.search(r'text="([^"]+)"[^>]*resource-id="[^"]*tvPropName"', xml)
        if m:
            return clean(m.group(1)) or None

        titles = re.findall(r'text="([^"]{8,80})"', xml)
        skip = (
            "搜索",
            "关注",
            "推荐",
            "购买",
            "探索",
            "得物",
            "登录",
            "分享",
            "发售",
            "配色",
            "评价",
            "尺码",
            "详情",
        )
        for t in titles:
            t = clean(t)
            if any(s in t for s in skip):
                continue
            if "¥" in t or "￥" in t:
                continue
            if re.search(r"[\u4e00-\u9fffA-Za-z]", t):
                return t
        return clean(titles[0]) if titles else None

    def _is_plausible_size(self, text: str) -> bool:
        """过滤评价页点赞数等噪音，只保留像尺码的值。"""
        t = (text or "").strip().upper()
        if not t:
            return False
        if t in self.CLOTHING_SIZES:
            return True
        try:
            n = float(t)
        except ValueError:
            return False
        # 鞋码常见 33-50（含半码）；部分童鞋/海外码会更宽一点。
        if 33 <= n <= 52:
            return True
        # 童鞋偶发更小。
        if 24 <= n <= 32:
            return True
        return False

    def _extract_article_number(self, xml: str) -> str | None:
        m = re.search(r"货号[:：]\s*([A-Za-z0-9\-_/]+)", xml)
        if m:
            return m.group(1)
        m = re.search(
            r'resource-id="[^"]*tvArticleNumber"[^>]*text="([^"]+)"',
            xml,
        )
        if m:
            return re.sub(r"^货号[:：]\s*", "", m.group(1)).strip() or None
        m = re.search(
            r'text="([^"]+)"[^>]*resource-id="[^"]*tvArticleNumber"',
            xml,
        )
        if m:
            return re.sub(r"^货号[:：]\s*", "", m.group(1)).strip() or None
        return None

    @staticmethod
    def _extract_article_numbers(xml: str) -> list[str]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        numbers = []
        for node in root.iter("node"):
            text = (node.get("text") or "").strip()
            resource = node.get("resource-id", "").rsplit("/", 1)[-1]
            if resource not in ("tvMainNumber", "tvSubNumber", "tvArticleNumber") and not re.match(
                r"^(?:主货号|辅助货号|货号)[:：]", text
            ):
                continue
            text = re.sub(r"^(?:主货号|辅助货号|货号)[:：]\s*", "", text)
            for token in re.split(r"[,，、;\s]+", text):
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]*", token) and token not in numbers:
                    numbers.append(token)
        return numbers

    def _verify_sku_on_sell_sheet(self, expected: str, xml: str) -> bool:
        actual = self._extract_article_number(xml)
        if self._sku_matches(expected, actual):
            return True
        if actual and any(self._sku_matches(actual, n) for n in self._confirmed_articles):
            if any(self._sku_matches(expected, n) for n in self._confirmed_articles):
                return True
        more = self.d(resourceId="com.shizhuang.duapp:id/tvMoreArticleNumbers")
        if more.exists(timeout=0):
            more.click()
            if self._wait_until(
                lambda: "是这款" in self._page_text(), timeout=4
            ):
                self._confirm_sku_match_dialog()
                return bool(actual) and any(
                    self._sku_matches(actual, n) for n in self._confirmed_articles
                ) and any(self._sku_matches(expected, n) for n in self._confirmed_articles)
        return False

    @staticmethod
    def _normalize_sku(s: str) -> str:
        s = (s or "").strip().upper()
        s = re.sub(r"^货号[:：]\s*", "", s)
        return re.sub(r"[^A-Z0-9]", "", s)

    def _sku_matches(self, expected: str, actual: str | None) -> bool:
        if not actual:
            return False
        a = self._normalize_sku(expected)
        b = self._normalize_sku(actual)
        if not a or not b:
            return False
        return a == b

    def _click_visible_text(
        self,
        pattern: str,
        *,
        contains: bool = True,
        min_y: int = 0,
        max_y: int | None = None,
        x_offset: int = 0,
        y_offset: int = 0,
    ) -> bool:
        """Click by visible text bounds, even when the TextView itself is not clickable."""
        xml = self._page_text()
        candidates: list[dict] = []
        for m in self.NODE_RE.finditer(xml or ""):
            attrs = dict(self.ATTR_RE.findall(m.group(1)))
            text = (attrs.get("text") or attrs.get("content-desc") or "").strip()
            if not text:
                continue
            matched = pattern in text if contains else pattern == text
            if not matched:
                continue
            bm = re.match(
                r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds") or ""
            )
            if not bm:
                continue
            x1, y1, x2, y2 = map(int, bm.groups())
            if y1 < min_y:
                continue
            if max_y is not None and y2 > max_y:
                continue
            candidates.append(
                {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "text": text}
            )
        if not candidates:
            return False
        # Prefer the top-most visible match; these labels are usually section headers or tabs.
        node = sorted(candidates, key=lambda n: (n["y1"], n["x1"]))[0]
        w, h = self.d.window_size()
        x = min(max((node["x1"] + node["x2"]) // 2 + x_offset, 1), w - 2)
        y = min(max((node["y1"] + node["y2"]) // 2 + y_offset, 1), h - 2)
        self.d.click(x, y)
        time.sleep(0.9)
        return True

    def _close_sell_sheet(self) -> None:
        d = self.d
        if d(resourceId="com.shizhuang.duapp:id/tvCloseDialog").click_exists(
            timeout=0
        ):
            self._sleep(0.2)
            return
        xml = self._page_text()
        if "预计收入" in xml or "最低出售价" in xml or "去出价" in xml:
            d.press("back")
            time.sleep(0.6)

    def _click_recent_purchase_entry(self) -> bool:
        """Click the visible recent-buy/price tab on product detail without scrolling."""
        _, h = self.d.window_size()
        top_band = int(h * 0.22)
        for label in ("最近购买", "3日均价", "日均价"):
            if self._click_visible_text(label, min_y=0, max_y=top_band):
                return True
        card_band = int(h * 0.62)
        for label in ("最近购买", "3日均价", "日均价"):
            if self._click_visible_text(label, min_y=0, max_y=card_band):
                return True
        return False

    def _recover_to_search_results(self) -> bool:
        """关闭出售/详情，尽量回到搜索结果列表。成功返回 True。"""
        self._close_sell_sheet()
        d = self.d
        for _ in range(6):
            xml = self._page_text()
            if "预计收入" in xml or "最低出售价" in xml or "去出价" in xml:
                self._close_sell_sheet()
                continue
            act = d.app_current().get("activity", "") or ""
            if "ProductDetail" in act or "product_detail" in act or (
                "立即购买" in xml and "还价" in xml
            ):
                if d(
                    resourceId="com.shizhuang.duapp:id/homeAsUpBtn"
                ).click_exists(timeout=0.5):
                    time.sleep(0.55)
                else:
                    self._press_page_back()
                continue
            # 已在搜索结果：有价格列表、无详情主按钮
            if self._looks_like_search_results(xml):
                return True
            # 搜索输入页也算可继续
            if d(resourceId="com.shizhuang.duapp:id/etSearch").exists(timeout=0.25):
                return True
            break
        return self._looks_like_search_results(self._page_text())

    def _looks_like_search_results(self, xml: str | None = None) -> bool:
        xml = xml if xml is not None else self._page_text()
        if "立即购买" in xml and "还价" in xml:
            return False
        if "出售商品" in xml or "预计收入" in xml:
            return False
        if any(
            n.get("resource-id", "").rsplit("/", 1)[-1] in ("search_title", "tvProductPrice")
            for n in self._xml_nodes(xml)
        ):
            return True
        # 结果页常见：多个价格 + 综合/销量等筛选，或 Search 相关
        prices = len(re.findall(r"[¥￥]", xml))
        if prices >= 3 and (
            "综合" in xml or "销量" in xml or "新品" in xml or "筛选" in xml
        ):
            return True
        act = self.d.app_current().get("activity", "") or ""
        if "Search" in act and prices >= 2:
            return True
        return False

    def _back_to_main_tabs(self, progress: ProgressCb | None = None) -> bool:
        """退到有底栏的主界面（不必进推荐首页）。"""
        d = self.d
        self._close_sell_sheet()
        if self._is_huozhi_overlay():
            self._leave_huozhi_flow()
        for i in range(10):
            xml = self._page_text()
            if self._is_huozhi_overlay(xml):
                self._press_page_back()
                continue
            has_tabs = 'text="我"' in xml and (
                "得物" in xml or "购买" in xml or "探索" in xml
            )
            if has_tabs and "立即购买" not in xml and "出售商品" not in xml:
                if self._is_seller_me_page(xml) or self._is_buyer_me_page(xml):
                    self._tap_buyer_home_tab(progress)
                    xml = self._page_text()
                    has_tabs = 'text="我"' in xml and (
                        "得物" in xml or "购买" in xml or "探索" in xml
                    )
                    if not has_tabs:
                        continue
                self._progress(progress, "home", "已回到主界面")
                return True
            self._progress(progress, "home", f"退出当前页…({i + 1})")
            self._press_page_back()
        self._tap_buyer_home_tab(progress)
        xml = self._page_text()
        return 'text="我"' in xml or "得物" in xml or "购买" in xml

    def _prepare_sku_search(self, sku: str, progress: ProgressCb | None = None) -> None:
        """进入可搜索状态并搜货号：优先退出详情到搜索层，必要时才回首页。"""
        self._close_sell_sheet()
        if self._is_huozhi_overlay() or self._is_seller_me_page():
            self._progress(progress, "home", "退出商家页…")
            self._leave_huozhi_flow()
            self._ensure_buyer_mode(progress)
            self._tap_buyer_home_tab(progress)
        elif self._is_buyer_me_page():
            self._tap_buyer_home_tab(progress)

        recovered = self._recover_to_search_results()
        if recovered:
            self._progress(progress, "search", "已回到搜索层，直接搜货号")
        else:
            # 搜索框是否已可用
            d = self.d
            can_search = any(
                d(resourceId=f"com.shizhuang.duapp:id/{resource}").exists(timeout=0)
                for resource in ("etSearch", "flSearchB", "bgSearchView", "collapseSearchArea")
            ) or d(description="搜索").exists(timeout=0)
            if not can_search:
                self._progress(progress, "home", "无法直达搜索，返回首页…")
                self._go_home(progress)

        try:
            self._search_sku(sku, progress)
        except RuntimeError as exc:
            if "找不到搜索输入框" not in str(exc):
                raise
            self._progress(progress, "recover", "搜索入口未就绪，重新定位首页搜索栏…")
            self._go_home(progress)
            self._search_sku(sku, progress)

    def _search_sku(self, sku: str, progress: ProgressCb | None = None) -> None:
        sku = (sku or "").strip()
        if not sku:
            raise RuntimeError("货号不能为空")
        self._progress(progress, "search", f"打开搜索并输入货号 {sku}")
        self._tap_search_box()
        self._input_text(sku)
        got = self._read_search_input()
        if not self._sku_matches(sku, got):
            raise RuntimeError(
                f"提交前货号校验失败：期望「{sku}」，实际「{got or '空'}」"
            )
        self._progress(progress, "search_ok", f"货号已校验：{got}")
        self._submit_search()
        if not self._wait_until(lambda: self._looks_like_search_results(), timeout=10):
            raise RuntimeError("搜索结果未加载或未找到商品，请检查网络及货号")
        tab = self.d(resourceId="com.shizhuang.duapp:id/view_id_search_tab_title", text="商品")
        if tab.exists(timeout=0):
            tab.click()
            if not self._wait_until(lambda: bool(self._product_result_nodes()), timeout=8):
                raise RuntimeError("商品列表未加载，停止以免误点图文或推荐")

    def _product_result_nodes(self) -> list[dict]:
        w, h = self.d.window_size()
        nodes = self._xml_nodes(self._page_text())
        for resource in ("search_title", "tvProductPrice"):
            candidates = [
                n for n in nodes
                if n.get("resource-id", "").rsplit("/", 1)[-1] == resource
                and n.get("package") == "com.shizhuang.duapp"
                and 0 <= n["cx"] < w and h * 0.15 < n["cy"] < h
            ]
            if candidates:
                return sorted(candidates, key=lambda n: (n["y1"], n["x1"]))
        return []

    def _open_result_at(self, index: int = 0) -> None:
        """打开搜索结果中的第 index 个商品（0 起）。"""
        nodes = self._product_result_nodes()
        if not 0 <= index < len(nodes):
            raise RuntimeError("没有更多可核对的商品结果，未找到精确匹配货号")
        node = nodes[index]
        self.d.click(node["cx"], node["cy"])
        if not self._wait_product_detail():
            raise RuntimeError("点击商品后未进入详情页，停止解析以免读取错页")

    def _open_first_result(self) -> None:
        self._open_result_at(0)

    def _extract_detail_sell_price(self, xml: str) -> float | None:
        """详情页当前售价：取右栏顶部字号最大的价格（避免误抓发售价 899）。"""
        nodes: list[tuple[int, int, int, float]] = []  # area, y1, x1, price

        patterns = (
            r'text="([¥￥][^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*text="([¥￥][^"]*)"',
        )
        for pi, pat in enumerate(patterns):
            for m in re.finditer(pat, xml or ""):
                if pi == 0:
                    raw, x1, y1, x2, y2 = m.group(1), *map(int, m.groups()[1:])
                else:
                    x1, y1, x2, y2 = map(int, m.groups()[:4])
                    raw = m.group(5)
                n = self._parse_price_token(re.sub(r"[¥￥]", "", raw))
                if n is None:
                    continue
                # 右栏顶部价格带
                if y1 > 220 or x1 < 850:
                    continue
                # 跳过明显附属文案里的价（比发售价低¥201 等整句）
                if "低" in raw or "均价" in raw or len(raw) > 12:
                    continue
                area = max(1, (x2 - x1) * (y2 - y1))
                # 空格分隔大号价加权（¥ 4 9 8）
                if re.search(r"[¥￥]\s*(?:\d\s+){1,}\d", raw):
                    area *= 3
                nodes.append((area, y1, x1, n))

        if nodes:
            nodes.sort(key=lambda t: (-t[0], t[1], t[2]))
            return nodes[0][3]

        # 兜底：空格大号价
        for m in re.finditer(r"[¥￥]\s*((?:\d\s+){1,}\d)", xml or ""):
            n = self._parse_price_token(m.group(1))
            if n is not None:
                return n
        return None

    def _parse_trade_hours_ago(self, text: str) -> float | None:
        t = (text or "").strip()
        if not t:
            return None
        if "刚刚" in t or "刚才" in t:
            return 0.0
        if "昨天" in t:
            return 24.0
        m = re.search(r"(\d+)\s*分钟前", t)
        if m:
            return int(m.group(1)) / 60.0
        m = re.search(r"(\d+)\s*小时前", t)
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+)\s*天前", t)
        if m:
            return float(m.group(1)) * 24.0
        return None

    def _extract_size_from_spec(self, text: str) -> str | None:
        """从『麂皮黑 46』这类规格里抽出尺码。"""
        t = (text or "").strip()
        pattern = r"(?:^|[\s/])(?P<size>\d{2,3}(?:\.\d)?|XXS|XS|S|M|L|XL|XXL|XXXL|XXXXL|均码|ONE SIZE)\s*$"
        m = re.search(pattern, t, re.I)
        if not m:
            return None
        size = m.group("size")
        if self._is_plausible_size(size):
            return size.upper() if re.search(r"[A-Za-z]", size) else size
        return None

    def _extract_text_nodes(self, xml: str) -> list[dict]:
        nodes: list[dict] = []
        for m in self.NODE_RE.finditer(xml or ""):
            attrs = dict(self.ATTR_RE.findall(m.group(1)))
            text = (attrs.get("text") or "").strip().replace("&#10;", " ")
            bm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds") or "")
            if not text or not bm:
                continue
            x1, y1, x2, y2 = map(int, bm.groups())
            nodes.append(
                {
                    "text": re.sub(r"\s+", " ", text).strip(),
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "cx": (x1 + x2) // 2,
                    "cy": (y1 + y2) // 2,
                }
            )
        return nodes

    def _price_from_text(self, text: str) -> tuple[float | None, str] | None:
        text = (text or "").strip()
        if not text:
            return None
        if text in ("--", "—", "¥--", "￥--", "¥—", "￥—"):
            return None, "¥--"
        m = self.PRICE_FLEX_RE.search(text)
        if not m:
            return None
        price = self._parse_price_token(m.group(1))
        raw = m.group(0).replace("￥", "¥")
        return price, raw

    def _size_from_text(self, text: str) -> str | None:
        t = (text or "").strip()
        if self.SIZE_RE.fullmatch(t) and self._is_plausible_size(t):
            return t.upper() if re.search(r"[A-Za-z]", t) else t
        return self._extract_size_from_spec(t)

    def _extract_recent_trades(self, xml: str) -> list[dict]:
        texts = [t.strip() for t in re.findall(r'text="([^"]*)"', xml or "") if t.strip()]
        trades: list[dict] = []
        i = 0
        while i < len(texts):
            size = self._extract_size_from_spec(texts[i])
            if not size:
                i += 1
                continue
            price = None
            hours = None
            bargained = False
            # 向后看几项找价格与时间
            for j in range(i + 1, min(i + 8, len(texts))):
                tj = texts[j]
                if price is None and (tj.startswith("¥") or tj.startswith("￥")):
                    price = self._parse_price_token(re.sub(r"[¥￥]", "", tj))
                    continue
                if tj == "还价":
                    bargained = True
                    continue
                h = self._parse_trade_hours_ago(tj)
                if h is not None:
                    hours = h
                    break
                # 遇到下一条规格则停
                if self._extract_size_from_spec(tj):
                    break
            if price is not None and hours is not None:
                trades.append(
                    {
                        "size": size,
                        "price": price,
                        "hours_ago": hours,
                        "bargained": bargained,
                        "spec": texts[i],
                    }
                )
                i += 1
                continue
            i += 1
        return trades

    def _aggregate_sales_by_size(self, trades: list[dict]) -> dict:
        out: dict[str, dict] = {}
        for tr in trades:
            size = str(tr.get("size") or "")
            if not size:
                continue
            bucket = out.setdefault(
                size,
                {
                    "sales_1d": 0,
                    "sales_3d": 0,
                    "sales_7d": 0,
                    "sales_total_seen": 0,
                },
            )
            hours = float(tr.get("hours_ago") or 0)
            bucket["sales_total_seen"] += 1
            if hours <= 24:
                bucket["sales_1d"] += 1
            if hours <= 72:
                bucket["sales_3d"] += 1
            if hours <= 168:
                bucket["sales_7d"] += 1
        return out

    def _parse_sales_count(self, raw: str) -> int | None:
        """解析『773』『1.92w』『1.92万』。"""
        t = (raw or "").strip().replace(",", "").replace(" ", "")
        if not t:
            return None
        m = re.match(r"^([\d.]+)([Ww万])?$", t)
        if not m:
            return None
        try:
            n = float(m.group(1))
        except ValueError:
            return None
        if m.group(2):
            n *= 10000.0
        return int(round(n))

    def _tap_buyer_home_tab(self, progress: ProgressCb | None = None) -> bool:
        """点底栏买家首页入口；新版常叫『购买』，旧版可能叫『得物』。"""
        d = self.d
        w, h = d.window_size()
        self._progress(progress, "home", "点击底部购买/得物…")

        for label in ("购买", "得物"):
            try:
                nodes = d(text=label)
                if nodes.exists(timeout=0.35):
                    for i in range(nodes.count):
                        try:
                            b = (nodes[i].info or {}).get("bounds") or {}
                            top = int(b.get("top") or 0)
                            left = int(b.get("left") or 0)
                            right = int(b.get("right") or 0)
                            if top >= int(h * 0.82) and right > left:
                                d.click((left + right) // 2, int(h * 0.96))
                                time.sleep(0.9)
                                return True
                        except Exception:
                            continue
            except Exception:
                pass

        for rid in ("tvMall", "tvTrend"):
            try:
                if d(resourceId=f"com.shizhuang.duapp:id/{rid}").click_exists(timeout=0.35):
                    time.sleep(0.9)
                    return True
            except Exception:
                pass

        # 底栏常见四等分：得物 / 购买 / 探索 / 我；优先购买。
        d.click(int(w * 0.375), int(h * 0.96))
        time.sleep(0.9)
        return True

    def _tap_me_tab(self) -> bool:
        """点底栏『我』；必须先露出底栏。"""
        d = self.d
        w, h = d.window_size()
        tab = d(resourceId="com.shizhuang.duapp:id/tvUser")
        if tab.exists(timeout=0):
            if tab.info.get("selected"):
                return True
            tab.click()
            return self._wait_until(
                lambda: bool(d(resourceId="com.shizhuang.duapp:id/tvUser").info.get("selected")),
                timeout=4,
            )

        # 先退出出售/详情/穿搭帖等无底栏页
        for _ in range(8):
            xml = self._page_text()
            if 'text="我"' in xml and ("得物" in xml or "购买" in xml):
                break
            if any(
                k in xml
                for k in (
                    "去出价",
                    "预计收入",
                    "出售商品",
                    "最低出售价",
                    "规格销量排序",
                    "立即购买",
                    "说点什么",
                )
            ):
                self._press_page_back()
                continue
            if 'text="我"' not in xml:
                self._press_page_back()
                continue
            break

        # 优先点屏幕底部的『我』（避免点到正文里的字）
        try:
            nodes = d(text="我")
            if nodes.exists(timeout=1.0):
                for i in range(nodes.count):
                    try:
                        b = (nodes[i].info or {}).get("bounds") or {}
                        top = int(b.get("top") or 0)
                        if top >= int(h * 0.85):
                            nodes[i].click()
                            time.sleep(1.1)
                            return True
                    except Exception:
                        continue
                nodes.click()
                time.sleep(1.1)
                return True
        except Exception:
            pass

        # 底栏第 4 格兜底（得物/购买/探索/我）
        d.click(int(w * 0.90), int(h * 0.96))
        time.sleep(1.1)
        return True

    def _is_seller_me_page(self, xml: str | None = None) -> bool:
        xml = xml if xml is not None else self._page_text()
        if "去出价" in xml or "出售商品" in xml or "说点什么" in xml:
            return False
        if "货值计算" in xml:
            return True
        if "我要出售" in xml and ("待发货" in xml or "仓发管理" in xml):
            return True
        if "切换买家版" in xml or "切换到买家版" in xml:
            return True
        return False

    def _is_buyer_me_page(self, xml: str | None = None) -> bool:
        xml = xml if xml is not None else self._page_text()
        if "去出价" in xml or "出售商品" in xml:
            return False
        if "切换商家版" in xml:
            return True
        return "待付款" in xml and "待收货" in xml and any(
            n.get("resource-id", "").endswith("/tvUser") and n.get("selected") == "true"
            for n in self._xml_nodes(xml)
        )

    def _tap_visible_node_text(
        self,
        text: str,
        *, 
        contains: bool = False,
        x_offset: int = 0,
        timeout: float = 0.6,
    ) -> bool:
        d = self.d
        selector = d(textContains=text) if contains else d(text=text)
        try:
            if not selector.exists(timeout=timeout):
                return False
            w, h = d.window_size()
            for i in range(selector.count):
                node = selector[i]
                b = (node.info or {}).get("bounds") or {}
                x1 = int(b.get("left") or 0)
                y1 = int(b.get("top") or 0)
                x2 = int(b.get("right") or 0)
                y2 = int(b.get("bottom") or 0)
                if y2 <= 0 or y1 >= h or x2 <= 0 or x1 >= w:
                    continue
                if x_offset:
                    d.click(min(max(x2 + x_offset, 12), w - 12), (y1 + y2) // 2)
                else:
                    node.click()
                time.sleep(1.5)
                return True
            return False
        except Exception:
            return False

    def _find_and_tap_seller_switch(
        self, progress: ProgressCb | None = None, max_swipes: int = 6
    ) -> bool:
        d = self.d
        w, h = d.window_size()
        for i in range(max_swipes + 1):
            xml = self._page_text()
            if self._is_seller_me_page(xml):
                return True
            for label in ("切换商家版", "商家版"):
                if self._tap_visible_node_text(label, contains=(label != "切换商家版")):
                    if self._is_seller_me_page():
                        return True
                    time.sleep(0.6)
            if self._click_visible_text("切换商家版", x_offset=32):
                time.sleep(1.2)
                if self._is_seller_me_page():
                    return True
            if i >= max_swipes:
                break
            self._progress(progress, "seller", f"向下寻找切换商家版…({i + 1}/{max_swipes})")
            d.swipe(int(w * 0.72), int(h * 0.78), int(w * 0.72), int(h * 0.36), 0.28)
            time.sleep(0.65)
        return False

    def _ensure_seller_mode(self, progress: ProgressCb | None = None) -> bool:
        """回首页 → 『我』 → 判断/切换商家版。"""
        d = self.d
        self._close_sell_sheet()

        xml0 = self._page_text()
        need_home = (
            'text="我"' not in xml0
            or "立即购买" in xml0
            or "说点什么" in xml0
            or "出售商品" in xml0
            or "去出价" in xml0
            or (
                "主货号" in xml0
                and "切换商家版" not in xml0
                and "货值计算" not in xml0
            )
        )
        if need_home:
            self._progress(progress, "home", "回到主界面再进商家版…")
            self._back_to_main_tabs(progress)

        self._progress(progress, "seller", "进入『我』…")
        self._tap_me_tab()

        xml = self._page_text()
        if self._is_seller_me_page(xml):
            self._progress(progress, "seller", "已在商家版")
            return True

        if "切换商家版" not in xml and "商家版" not in xml and not self._is_buyer_me_page(xml):
            self._progress(progress, "seller", "未到『我』页，重试…")
            self._back_to_main_tabs(progress)
            self._tap_me_tab()
            time.sleep(0.9)
            xml = self._page_text()
            if self._is_seller_me_page(xml):
                self._progress(progress, "seller", "已在商家版")
                return True

        self._progress(progress, "seller", "切换商家版…")
        if not self._find_and_tap_seller_switch(progress):
            self._progress(progress, "seller", "未找到切换商家版入口")
            return False

        xml = self._page_text()
        if self._is_seller_me_page(xml):
            self._progress(progress, "seller", "已进入商家版")
            return True
        self._progress(progress, "seller", "未能进入商家版")
        return False

    def _ensure_buyer_mode(self, progress: ProgressCb | None = None) -> None:
        """查完销量后切回买家版，避免影响下次货号搜索。"""
        d = self.d
        # 先关掉货值浮层，否则点不到切换
        if self._is_huozhi_overlay():
            self._leave_huozhi_flow()
        self._tap_me_tab()
        xml = self._page_text()
        if self._is_buyer_me_page(xml) or not self._is_seller_me_page(xml):
            self._tap_buyer_home_tab(progress)
            return
        self._progress(progress, "buyer", "切回买家版…")
        for sel in (
            dict(text="切换买家版"),
            dict(textContains="切换买家"),
            dict(textContains="买家版"),
            dict(descriptionContains="切换买家"),
            dict(descriptionContains="买家版"),
        ):
            try:
                if d(**sel).click_exists(timeout=0.8):
                    time.sleep(1.4)
                    if self._is_buyer_me_page() or not self._is_seller_me_page():
                        self._tap_buyer_home_tab(progress)
                        return
            except Exception:
                continue
        # 标题旁切换图标兜底（商家页顶部）
        for label in ("出售", "卖家中心", "商家中心"):
            node = d(text=label)
            if not node.exists(timeout=0.3):
                continue
            try:
                info = node.info
                b = info.get("bounds") or {}
                x2 = int(b.get("right") or 0)
                y1 = int(b.get("top") or 0)
                y2 = int(b.get("bottom") or 0)
                if x2 > 0 and y2 < 420:
                    d.click(min(x2 + 48, d.window_size()[0] - 20), (y1 + y2) // 2)
                    time.sleep(1.4)
                    break
            except Exception:
                pass
        if self._is_buyer_me_page() or not self._is_seller_me_page():
            self._tap_buyer_home_tab(progress)

    def _open_huozhi(self, progress: ProgressCb | None = None) -> bool:
        d = self.d
        self._progress(progress, "huozhi", "打开货值计算…")
        if "货值计算" in self._page_text() and (
            "最近修改" in self._page_text() or "销量排序" in self._page_text()
        ):
            return True
        if d(text="货值计算").click_exists(timeout=2.0):
            time.sleep(1.6)
            return True
        if d(textContains="货值计算").click_exists(timeout=1.0):
            time.sleep(1.6)
            return True
        return "货值计算" in self._page_text()

    def _search_huozhi_sku(self, sku: str, progress: ProgressCb | None = None) -> bool:
        d = self.d
        self._progress(progress, "huozhi_search", f"货值计算搜索 {sku}")
        opened = False
        for sel in (
            dict(description="搜索"),
            dict(descriptionContains="搜索"),
            dict(resourceId="com.shizhuang.duapp:id/ivSearch"),
        ):
            try:
                if d(**sel).click_exists(timeout=0.8):
                    opened = True
                    time.sleep(0.8)
                    break
            except Exception:
                continue
        if not opened:
            # 货值计算顶栏右侧放大镜（右半屏）
            w, h = d.window_size()
            for fx, fy in ((0.84, 0.08), (0.88, 0.09), (0.80, 0.08)):
                d.click(int(w * fx), int(h * fy))
                time.sleep(0.7)
                if d(className="android.widget.EditText").exists(timeout=0.5):
                    opened = True
                    break
        et = d(className="android.widget.EditText")
        if not et.exists(timeout=1.2):
            return False

        et.click()
        time.sleep(0.25)
        try:
            et.set_text(sku)
        except Exception:
            try:
                d.set_fastinput_ime(True)
            except Exception:
                pass
            try:
                d.send_keys(sku, clear=True)
            except Exception:
                return False
        time.sleep(0.4)

        # 注意：输入框内也有 text=搜索 的 Image（不可点），必须点右侧 TextView
        submitted = False
        btn = d(
            text="搜索",
            className="android.widget.TextView",
            clickable=True,
        )
        if btn.exists(timeout=1.2):
            btn.click()
            submitted = True
            time.sleep(1.6)
        elif d(text="搜索", className="android.widget.TextView").click_exists(
            timeout=0.8
        ):
            submitted = True
            time.sleep(1.6)
        else:
            # 坐标点右侧「搜索」文案
            w, h = d.window_size()
            d.click(int(w * 0.96), int(h * 0.08))
            submitted = True
            time.sleep(1.4)

        # 若仍停在历史记录，点历史标签再试一次提交
        xml = self._page_text()
        if "历史记录" in xml:
            # 历史里的货号 chip（非输入框）
            chips = d(text=sku, className="android.widget.TextView")
            if chips.exists(timeout=0.6):
                try:
                    chips[-1].click()
                except Exception:
                    chips.click()
                time.sleep(1.4)
            elif btn.exists(timeout=0.5):
                btn.click()
                time.sleep(1.4)

        # 等到离开「历史记录」搜索页，出现结果或规格行情
        for _ in range(12):
            xml = self._page_text()
            if "规格行情" in xml or "7日销量" in xml or "缺货" in xml:
                return True
            if "历史记录" not in xml and (
                sku.replace("-", "")[:6] in xml.replace("-", "")
                or "库存" in xml
                or "立即出价" in xml
            ):
                return True
            time.sleep(0.45)
        return "历史记录" not in self._page_text()

    def _open_first_huozhi_result(self, progress: ProgressCb | None = None) -> bool:
        d = self.d
        self._progress(progress, "huozhi_open", "打开第一条规格行情…")
        xml = self._page_text()
        if "规格销量排序" in xml or (
            "商品详情" in xml and ("现货价" in xml or "7日销量" in xml)
        ):
            return True

        # 优先点第一条上的「规格行情」
        btns = d(text="规格行情")
        if btns.exists(timeout=2.5):
            try:
                btns[0].click()
            except Exception:
                btns.click()
            time.sleep(1.8)
        else:
            # 点含货号的首条商品区域（右半屏列表上部）
            w, h = d.window_size()
            for fx, fy in ((0.72, 0.30), (0.72, 0.38), (0.68, 0.28)):
                d.click(int(w * fx), int(h * fy))
                time.sleep(1.4)
                xml = self._page_text()
                if "规格行情" in xml or "规格销量排序" in xml or "商品详情" in xml:
                    break

        # 若停在列表卡片，再点规格行情
        if d(text="规格行情").click_exists(timeout=1.0):
            time.sleep(1.5)

        for _ in range(8):
            xml = self._page_text()
            if "规格销量排序" in xml or (
                "7日销量" in xml and ("现货价" in xml or "入仓价" in xml)
            ):
                return True
            if "规格行情" in xml and "商品详情" in xml:
                d(text="规格行情").click_exists(timeout=0.6)
                time.sleep(0.8)
            time.sleep(0.4)
        return False

    def _extract_merchant_size_sales(self, xml: str) -> dict:
        """从商家版商品详情『规格销量排序』解析各码 7 日销量。

        WebView 里常见拆开节点：有原装鞋盒 40 / 7日 / 销量 / 299
        """
        out: dict[str, dict] = {}

        def put(size: str, sales7: int | None) -> None:
            if not size or sales7 is None:
                return
            if not (
                self._is_plausible_size(size) or size.upper() in self.CLOTHING_SIZES
            ):
                return
            out[str(size)] = {
                "sales_1d": 0,
                "sales_3d": 0,
                "sales_7d": int(sales7),
                "sales_total_seen": int(sales7),
            }

        # 按卡片块解析（带 bounds，过滤顶部被裁切的回收行）
        # 找到每个规格 TextView，再在后续兄弟文案里找 7日/销量/数字
        size_nodes: list[tuple[str, int, int, int]] = []  # size, y1, y2, endpos
        for m in re.finditer(
            r'text="((?:有原装鞋盒|无原装鞋盒)\s*[^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml or "",
        ):
            label, xa, ya, xb, yb = m.group(1), *map(int, m.groups()[1:])
            # 行高过小=被裁切的回收节点，易误绑整款汇总销量
            if (yb - ya) < 18:
                continue
            size = self._extract_size_from_spec(label)
            if size:
                size_nodes.append((size, ya, yb, m.end()))

        for idx, (size, ya, yb, endpos) in enumerate(size_nodes):
            # 取到下一规格之前的片段
            stop = size_nodes[idx + 1][3] if idx + 1 < len(size_nodes) else endpos + 800
            chunk = xml[endpos:stop]
            texts = [
                t.strip()
                for t in re.findall(r'text="([^"]*)"', chunk)
                if t.strip()
            ]
            sales7: int | None = None
            for j, tj in enumerate(texts):
                if (
                    tj == "7日"
                    and j + 2 < len(texts)
                    and texts[j + 1] == "销量"
                ):
                    sales7 = self._parse_sales_count(texts[j + 2])
                    break
                if "7日销量" in tj:
                    mm = re.search(r"7日销量\s*([\d.]+)\s*([Ww万])?", tj)
                    if mm:
                        sales7 = self._parse_sales_count(
                            mm.group(1) + (mm.group(2) or "")
                        )
                    break
            put(size, sales7)

        if out:
            return out

        # 兜底：纯文本序列（无 bounds 时）
        texts = [t.strip() for t in re.findall(r'text="([^"]*)"', xml or "") if t.strip()]
        i = 0
        while i < len(texts):
            size = self._extract_size_from_spec(texts[i])
            if not size:
                i += 1
                continue
            sales7 = None
            for j in range(i + 1, min(i + 20, len(texts))):
                if self._extract_size_from_spec(texts[j]):
                    break
                if (
                    texts[j] == "7日"
                    and j + 2 < len(texts)
                    and texts[j + 1] == "销量"
                ):
                    sales7 = self._parse_sales_count(texts[j + 2])
                    break
            put(size, sales7)
            i += 1
        return out

    def _collect_merchant_size_sales_with_scroll(self) -> dict:
        merged: dict[str, dict] = {}
        w, h = self.d.window_size()
        d = self.d

        # 确保在规格行情；默认已是 7 日，不要乱点「7日」（会点到汇总文案）
        d(text="规格行情").click_exists(timeout=0.8)
        time.sleep(0.4)

        def absorb() -> int:
            before = len(merged)
            xml = self._page_text()
            for size, row in self._extract_merchant_size_sales(xml).items():
                merged[size] = row
            # 调试：首屏无结果时落盘，便于修解析
            if before == 0 and len(merged) == 0:
                try:
                    path = settings.screenshot_dir / "_merchant_absorb.xml"
                    path.write_text(xml, encoding="utf-8")
                except Exception:
                    pass
            return len(merged) - before

        # 先轻轻上滑露出「规格销量排序」
        d.swipe(int(w * 0.72), int(h * 0.70), int(w * 0.72), int(h * 0.45), 0.25)
        time.sleep(0.4)
        absorb()
        stable = 0
        for _ in range(8):
            d.swipe(int(w * 0.72), int(h * 0.82), int(w * 0.72), int(h * 0.48), 0.28)
            time.sleep(0.45)
            added = absorb()
            if added == 0:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
        return merged

    def _press_page_back(self) -> None:
        """优先点页面左上角返回，系统 back 对货值浮层经常无效。"""
        d = self.d
        xml = self._page_text()
        if "再按一次返回键退出" in xml:
            return

        if d(description="转到上一层级").click_exists(timeout=0):
            self._sleep(0.25)
            return
        if d(descriptionContains="转到上一层").click_exists(timeout=0):
            self._sleep(0.25)
            return
        if d(resourceId="com.shizhuang.duapp:id/homeAsUpBtn").click_exists(
            timeout=0
        ):
            self._sleep(0.25)
            return

        # 货值计算标题左侧返回箭头（右半屏浮层）
        if self._is_huozhi_overlay(xml):
            for label in ("货值计算", "商品详情"):
                node = d(text=label)
                if not node.exists(timeout=0.35):
                    continue
                try:
                    b = (node.info or {}).get("bounds") or {}
                    x1 = int(b.get("left") or 0)
                    y1 = int(b.get("top") or 0)
                    y2 = int(b.get("bottom") or 0)
                    if x1 > 200:
                        d.click(max(20, x1 - 48), (y1 + y2) // 2)
                        time.sleep(0.6)
                        return
                except Exception:
                    pass
            w, h = d.window_size()
            # 右半屏顶栏左侧
            d.click(int(w * 0.53), int(h * 0.08))
            time.sleep(0.55)
            return

        d.press("back")
        self._sleep(0.25)

    def _is_huozhi_overlay(self, xml: str | None = None) -> bool:
        """是否停在货值计算列表 / 搜索 / 商品详情浮层。"""
        xml = xml if xml is not None else self._page_text()
        if "规格销量排序" in xml:
            return True
        if "商品详情" in xml and (
            "现货价" in xml or "入仓价" in xml or "规格行情" in xml
        ):
            return True
        # 列表：有规格行情+立即出价，或标题区筛选
        if "规格行情" in xml and "立即出价" in xml and (
            "7日销量" in xml or "30日销量" in xml or "最近修改" in xml
        ):
            return True
        if "货值计算" in xml and (
            "最近修改" in xml or "销量排序" in xml or "筛选" in xml
        ):
            return True
        if "历史记录" in xml and "搜索你想要查询的商品" in xml:
            return True
        return False

    def _leave_huozhi_flow(self) -> None:
        d = self.d
        for _ in range(8):
            xml = self._page_text()
            on_search = d(className="android.widget.EditText").exists(
                timeout=0.25
            ) and ("历史记录" in xml or "搜索你想要查询的商品" in xml)
            if self._is_huozhi_overlay(xml) or on_search:
                self._press_page_back()
                continue
            # 已离开浮层（商家「我」或别处）
            return

    def _capture_merchant_sales(
        self,
        sku: str,
        progress: ProgressCb | None = None,
        stem: str = "sales",
    ) -> tuple[str | None, dict, list[dict]]:
        """尺码价采完后：回首页 → 我 → 商家版 → 货值计算 → 各码 7 日销量。"""
        sku = (sku or "").strip()
        if not sku:
            self._progress(progress, "sales", "无货号，跳过商家版销量")
            return None, {}, []

        self._progress(progress, "sales", "采价完成，准备读商家版销量…")
        self._close_sell_sheet()
        time.sleep(0.2)

        # 轻量退到有底栏界面即可，不必刷推荐首页
        self._progress(progress, "home", "退出详情，准备进『我』…")
        self._back_to_main_tabs(progress)

        if not self._ensure_seller_mode(progress):
            shot = self._save_screenshot(f"{stem}_seller_fail")
            return shot, {}, []

        if not self._open_huozhi(progress):
            shot = self._save_screenshot(f"{stem}_huozhi_fail")
            self._ensure_buyer_mode(progress)
            return shot, {}, []

        if not self._search_huozhi_sku(sku, progress):
            shot = self._save_screenshot(f"{stem}_huozhi_search_fail")
            self._leave_huozhi_flow()
            self._ensure_buyer_mode(progress)
            return shot, {}, []

        if not self._open_first_huozhi_result(progress):
            shot = self._save_screenshot(f"{stem}_huozhi_open_fail")
            self._leave_huozhi_flow()
            self._ensure_buyer_mode(progress)
            return shot, {}, []

        xml_detail = self._page_text()
        tokens = [
            token for node in self._xml_nodes(xml_detail)
            for token in re.findall(
                r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*", node.get("text", "")
            )
        ]
        if not any(self._sku_matches(sku, token) for token in tokens):
            self._progress(progress, "sales_mismatch", "商家商品货号未核对，停止读取销量")
            shot = self._save_screenshot(f"{stem}_sales_mismatch")
            self._leave_huozhi_flow()
            self._ensure_buyer_mode(progress)
            return shot, {}, []
        self._progress(progress, "sales", "下滑读取各码 7 日销量…")
        sales_by_size = self._collect_merchant_size_sales_with_scroll()
        try:
            xml_now = self._page_text()
            (settings.screenshot_dir / f"{stem}_sales.xml").write_text(
                xml_now, encoding="utf-8"
            )
            if not sales_by_size:
                sales_by_size = self._extract_merchant_size_sales(xml_now)
        except Exception:
            pass
        shot = self._save_screenshot(f"{stem}_sales")

        self._leave_huozhi_flow()
        self._ensure_buyer_mode(progress)

        self._progress(
            progress,
            "sales_ok",
            f"商家版：已读 {len(sales_by_size)} 个尺码的 7 日销量",
        )
        return shot, sales_by_size, []

    def _capture_recent_sales(
        self, progress: ProgressCb | None = None, stem: str = "sales"
    ) -> tuple[str | None, dict, list[dict]]:
        """关闭出售弹层后，进入最近购买：截图 + 解析各码销量。"""
        d = self.d
        self._progress(progress, "sales", "打开最近购买…")
        self._close_sell_sheet()
        time.sleep(0.5)

        d(text="商品").click_exists(timeout=0.8)
        time.sleep(0.4)

        w, h = d.window_size()
        opened = False

        def wait_recent_page(timeout: float = 2.5) -> bool:
            deadline = time.time() + timeout
            while time.time() < deadline:
                act_now = d.app_current().get("activity") or ""
                xml_now = self._page_text()
                if "RecentBuy" in act_now:
                    return True
                if "最近购买" in xml_now and (
                    "小时前" in xml_now
                    or "天前" in xml_now
                    or ("全部" in xml_now and "viewPager" in xml_now)
                ):
                    return True
                time.sleep(0.25)
            return False

        act = d.app_current().get("activity") or ""
        if "RecentBuy" in act:
            opened = True

        if not opened:
            if self._click_recent_purchase_entry():
                opened = wait_recent_page()

        if not opened:
            for sel in (
                dict(textContains="3日均价"),
                dict(textContains="日均价"),
                dict(textContains="最近购买"),
                dict(textMatches=r".*均价.*"),
            ):
                if d(**sel).click_exists(timeout=1.2):
                    opened = wait_recent_page()
                    if opened:
                        break
            if not opened:
                # Some visible labels are plain TextViews; tap their bounds or nearby card area.
                for label in ("最近购买", "3日均价", "日均价"):
                    if self._click_visible_text(label, min_y=0, max_y=int(h * 0.62)):
                        opened = wait_recent_page()
                        if opened:
                            break

        if not opened:
            # 入口多半在详情中部，最多轻滑 2 次，避免一直往下翻
            for _ in range(2):
                xml = self._page_text()
                if "最近购买" in xml or "3日均价" in xml or "日均价" in xml:
                    if d(textContains="3日均价").click_exists(timeout=0.8):
                        opened = wait_recent_page()
                        if opened:
                            break
                    if d(textContains="最近购买").click_exists(timeout=0.8):
                        opened = wait_recent_page()
                        if opened:
                            break
                    clicked = (
                        self._click_visible_text("最近购买", min_y=0, max_y=int(h * 0.7))
                        or self._click_visible_text("3日均价", min_y=0, max_y=int(h * 0.7))
                        or self._click_visible_text("日均价", min_y=0, max_y=int(h * 0.7))
                    )
                    if not clicked:
                        d.click(int(w * 0.82), int(h * 0.55))
                        time.sleep(0.9)
                    opened = wait_recent_page()
                    if opened:
                        break
                d.swipe(int(w * 0.72), int(h * 0.62), int(w * 0.72), int(h * 0.42), 0.25)
                time.sleep(0.4)

        if not opened:
            self._progress(progress, "sales", "未找到最近购买入口，跳过销量")
            return None, {}, []

        for _ in range(4):
            xml = self._page_text()
            if "最近购买" in xml or "小时前" in xml or "天前" in xml:
                break
            time.sleep(0.35)

        # 尽量点「全部」尺码筛选；首屏足够估 1/7 日，少翻几页即可
        d(text="全部").click_exists(timeout=0.8)
        time.sleep(0.4)

        merged_trades: list[dict] = []
        seen_keys: set[str] = set()

        def absorb() -> int:
            xml = self._page_text()
            added = 0
            for tr in self._extract_recent_trades(xml):
                key = f"{tr['size']}|{tr['price']}|{tr['hours_ago']}|{tr.get('spec')}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged_trades.append(tr)
                added += 1
            return added

        absorb()
        shot = self._save_screenshot(f"{stem}_sales")

        # 最多再滑 2 次；无新增立刻停
        for _ in range(2):
            d.swipe(int(w * 0.5), int(h * 0.75), int(w * 0.5), int(h * 0.48), 0.25)
            time.sleep(0.4)
            if absorb() == 0:
                break

        sales_by_size = self._aggregate_sales_by_size(merged_trades)
        self._progress(
            progress,
            "sales_ok",
            f"最近购买：截图已存，解析成交 {len(merged_trades)} 条 / {len(sales_by_size)} 个尺码",
        )
        return shot, sales_by_size, merged_trades

    def _extract_size_prices(self, xml: str) -> list[dict]:
        """仅在出售弹层解析尺码-价格，避免评价页数字污染。"""
        if not xml:
            return []
        if not self._sell_sheet_open(xml):
            return []
        if not self._income_selected(xml):
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        cells: list[dict] = []
        for parent in root.iter("node"):
            children = list(parent)
            name = next((n for n in children if n.get("resource-id", "").endswith("/tvName")), None)
            price = next((n for n in children if n.get("resource-id", "").endswith("/tvPrice")), None)
            if name is None or price is None:
                continue
            size = self._size_from_text(name.get("text", ""))
            parsed = self._price_from_text(price.get("text", ""))
            if size and parsed is not None:
                cells.append({"size": size, "price": parsed[0], "raw": parsed[1]})
        if cells:
            return cells

        by_bounds = self._extract_size_prices_by_bounds(xml)
        if len(by_bounds) >= 2 and any(r.get("price") is not None for r in by_bounds):
            return by_bounds

        by_sequence = self._extract_size_prices_by_sequence(xml)
        if len(by_sequence) > len(by_bounds) or any(
            r.get("price") is not None for r in by_sequence
        ):
            return by_sequence

        texts = re.findall(r'text="([^"]*)"', xml)
        out: list[dict] = []
        seen: set[str] = set()
        i = 0
        while i < len(texts) - 1:
            size = texts[i].strip()
            nxt = texts[i + 1].strip()
            if (
                self._is_plausible_size(size)
                and self.SIZE_RE.fullmatch(size)
                and (
                    nxt.startswith("¥")
                    or nxt.startswith("￥")
                    or nxt in ("--", "—", "¥--", "￥--")
                )
            ):
                raw = nxt
                price = None
                if raw not in ("--", "—"):
                    price = self._parse_price_token(re.sub(r"[¥￥]", "", raw))
                if size not in seen:
                    seen.add(size)
                    out.append({"size": size, "price": price, "raw": raw})
                i += 2
                continue
            i += 1
        return out if out else by_bounds

    def _extract_size_prices_by_sequence(self, xml: str) -> list[dict]:
        nodes = [n for n in self._extract_text_nodes(xml) if n["y1"] >= 140]
        out: list[dict] = []
        seen: set[str] = set()
        for i, node in enumerate(nodes):
            size = self._size_from_text(node["text"])
            if not size or size in seen:
                continue
            best: tuple[float | None, str] | None = None
            for nxt in nodes[i + 1 : i + 8]:
                if self._size_from_text(nxt["text"]):
                    break
                parsed = self._price_from_text(nxt["text"])
                if parsed is None:
                    continue
                same_row = abs(nxt["cy"] - node["cy"]) <= 95
                below_nearby = node["cy"] - 20 <= nxt["cy"] <= node["cy"] + 140
                if same_row or below_nearby:
                    best = parsed
                    break
            if best is None:
                best = (None, "¥--")
            seen.add(size)
            out.append({"size": size, "price": best[0], "raw": best[1]})
        return out

    def _extract_size_prices_by_bounds(self, xml: str) -> list[dict]:
        sizes: list[dict] = []
        prices: list[dict] = []
        for m in self.NODE_RE.finditer(xml or ""):
            attrs = dict(self.ATTR_RE.findall(m.group(1)))
            text = (attrs.get("text") or "").strip()
            if not text:
                continue
            bm = re.match(
                r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds") or ""
            )
            if not bm:
                continue
            x1, y1, x2, y2 = map(int, bm.groups())
            # 出售弹层一般在屏幕中下部
            if y1 < 200:
                continue
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            size_text = self._size_from_text(text)
            if size_text:
                sizes.append(
                    {"size": size_text, "cx": cx, "cy": cy, "y1": y1, "x1": x1}
                )
            elif text.startswith("¥") or text.startswith("￥") or text in ("--", "—"):
                raw = text if text.startswith(("¥", "￥")) else f"¥{text}"
                price = None
                if text not in ("--", "—"):
                    price = self._parse_price_token(re.sub(r"[¥￥]", "", text))
                prices.append(
                    {
                        "raw": raw,
                        "price": price,
                        "cx": cx,
                        "cy": cy,
                        "y1": y1,
                        "x1": x1,
                    }
                )

        if not sizes:
            return []

        used: set[int] = set()
        out: list[dict] = []
        for s in sorted(sizes, key=lambda z: (z["y1"], z["x1"])):
            best_i = None
            best_dist = 1e18
            for i, p in enumerate(prices):
                if i in used:
                    continue
                dx = abs(p["cx"] - s["cx"])
                dy = p["cy"] - s["cy"]
                if dy < -25:
                    continue
                same_row = abs(dy) <= 45 and dx <= 180
                same_col_below = 0 <= dy <= 95 and dx <= 130
                if not same_row and not same_col_below:
                    continue
                dist = dx * 1.2 + abs(dy) * 3
                if dist < best_dist:
                    best_dist = dist
                    best_i = i
            if best_i is not None and best_dist < 320:
                used.add(best_i)
                p = prices[best_i]
                out.append(
                    {"size": s["size"], "price": p["price"], "raw": p["raw"]}
                )
            else:
                out.append({"size": s["size"], "price": None, "raw": "¥--"})
        return out

    def _wait_product_detail(self, timeout: float = 8.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            self._check_cancel()
            cur = self.d.app_current().get("activity", "") or ""
            # 识图结果列表 / 拍照搜索页不算详情
            if any(
                k in cur
                for k in ("PSResult", "GraphSearch", "TotalPublish", "PublishProcess")
            ):
                time.sleep(0.4)
                continue
            if "ProductDetail" in cur or "product_detail" in cur:
                return True
            xml = self._page_text()
            if "base_product_root" in xml and "PSResult" in cur:
                time.sleep(0.4)
                continue
            if "立即购买" in xml and ("还价" in xml or "出售" in xml):
                return True
            # 详情常见：带空格售价 + 出售入口（排除纯结果列表）
            if (
                "base_product_root" not in xml
                and re.search(r"[¥￥]\s*(?:\d\s+)+\d", xml)
                and ("出售" in xml or "还价" in xml or "主货号" in xml)
            ):
                return True
            time.sleep(0.5)
        return False

    def _grant_camera_permission(self) -> None:
        """预授权得物摄像头权限，减少弹窗。"""
        d = self.d
        for cmd in (
            "pm grant com.shizhuang.duapp android.permission.CAMERA",
            "appops set com.shizhuang.duapp CAMERA allow",
            "pm grant com.shizhuang.duapp android.permission.READ_EXTERNAL_STORAGE",
            "pm grant com.shizhuang.duapp android.permission.READ_MEDIA_IMAGES",
        ):
            try:
                d.shell(cmd)
            except Exception:
                pass

    def _click_ldplayer_window(self, nx: float, ny: float) -> bool:
        """在雷电窗口客户区相对坐标点击（处理宿主层摄像头弹窗）。"""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found: list[int] = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
            )

            def cb(hwnd, _lp):  # type: ignore[no-untyped-def]
                if not user32.IsWindowVisible(hwnd):
                    return True
                n = user32.GetWindowTextLengthW(hwnd) + 1
                buf = ctypes.create_unicode_buffer(n)
                user32.GetWindowTextW(hwnd, buf, n)
                title = buf.value or ""
                if "雷电" in title or "LDPlayer" in title.lower():
                    found.append(int(hwnd))
                return True

            user32.EnumWindows(WNDENUMPROC(cb), 0)
            if not found:
                return False
            hwnd = found[0]
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            pt = wintypes.POINT(
                int(rect.right * nx),
                int(rect.bottom * ny),
            )
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            user32.SetCursorPos(pt.x, pt.y)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def _dismiss_camera_request_dialog(
        self, progress: ProgressCb | None = None, *, aggressive: bool = True
    ) -> bool:
        """
        处理雷电「应用请求摄像头」弹窗（实时截取屏幕 / 开启摄像头）。
        该弹窗常不在无障碍树里，需坐标 / 宿主窗口点击。
        aggressive=False 时只点无障碍文案，避免回首页时误点相册缩略图。
        """
        d = self.d
        clicked = False

        for sel in (
            {"text": "实时截取屏幕"},
            {"textContains": "实时截取"},
            {"text": "开启摄像头"},
            {"textContains": "开启摄像头"},
            {"text": "允许"},
            {"text": "始终允许"},
            {"text": "仅在使用时允许"},
            {"text": "同意"},
            {"text": "确定"},
        ):
            if d(**sel).click_exists(timeout=0.35):
                self._progress(progress, "camera", f"已处理弹窗：{sel}")
                time.sleep(0.6)
                clicked = True
                break

        if clicked or not aggressive:
            return clicked

        on_graph = "GraphSearch" in (
            d.app_current().get("activity") or ""
        ) or "拍照搜索" in self._page_text()

        if on_graph:
            w, h = d.window_size()
            self._progress(progress, "camera", "尝试关闭摄像头请求弹窗…")
            # 设备坐标：弹窗中部左右按钮
            for fx, fy in (
                (0.40, 0.54),
                (0.38, 0.56),
                (0.42, 0.52),
                (0.60, 0.54),
            ):
                d.click(int(w * fx), int(h * fy))
                time.sleep(0.25)
            # 宿主窗口再点一次（雷电有时把弹窗画在 Windows 层）
            for nx, ny in ((0.40, 0.52), (0.42, 0.55), (0.58, 0.52)):
                if self._click_ldplayer_window(nx, ny):
                    time.sleep(0.25)
            clicked = True
            time.sleep(0.35)

        return clicked

    def _open_photo_search(self, progress: ProgressCb | None = None) -> bool:
        """打开首页搜索栏旁的「拍照搜索」，禁止点到发布用相机。"""
        d = self.d
        for _ in range(3):
            act = d.app_current().get("activity", "") or ""
            if "Publish" in act:
                d.press("back")
                time.sleep(0.6)
            else:
                break

        self._grant_camera_permission()
        self._progress(progress, "camera", "打开拍照搜索…")
        if d(resourceId="com.shizhuang.duapp:id/ivPhotoSearch").click_exists(
            timeout=2.0
        ):
            time.sleep(1.0)
        else:
            # 搜索框右侧相机图标（在「搜索」按钮左侧）
            w, h = d.window_size()
            d.click(int(w * 0.88), int(h * 0.075))
            time.sleep(1.0)

        # 雷电摄像头请求弹窗
        self._dismiss_camera_request_dialog(progress)
        time.sleep(0.4)
        self._dismiss_camera_request_dialog(progress)

        act = d.app_current().get("activity", "") or ""
        if "GraphSearch" in act:
            return True
        if d(resourceId="com.shizhuang.duapp:id/tvAlbum").exists(timeout=1.0):
            return True
        if d(text="拍照搜索").exists(timeout=0.5):
            return True
        # 若误进发布页，退回再试一次
        if "Publish" in act:
            d.press("back")
            time.sleep(0.8)
            if d(resourceId="com.shizhuang.duapp:id/ivPhotoSearch").click_exists(
                timeout=1.5
            ):
                time.sleep(1.0)
                self._dismiss_camera_request_dialog(progress)
                act = d.app_current().get("activity", "") or ""
                return "GraphSearch" in act
        return False

    def _pick_album_image_for_search(
        self, progress: ProgressCb | None = None
    ) -> bool:
        """在拍照搜索页选「我的相册」第一张图。"""
        d = self.d
        self._progress(progress, "pick", "选择相册图片…")
        self._dismiss_camera_request_dialog(progress)

        d(resourceId="com.shizhuang.duapp:id/tvAlbum").click_exists(timeout=1.2)
        time.sleep(0.45)

        toggle = d(resourceId="com.shizhuang.duapp:id/tvToggleButton")
        if toggle.exists(timeout=0.5):
            try:
                label = (toggle.get_text() or "").strip()
            except Exception:
                label = ""
            if label == "展开":
                toggle.click()
                time.sleep(0.7)

        thumbs = d(resourceId="com.shizhuang.duapp:id/imgThumb")
        if not thumbs.exists(timeout=2.5) or thumbs.count < 1:
            # 再点一次最近/相册刷新
            d(resourceId="com.shizhuang.duapp:id/tvRecently").click_exists(timeout=0.5)
            time.sleep(0.3)
            d(resourceId="com.shizhuang.duapp:id/tvAlbum").click_exists(timeout=0.5)
            time.sleep(0.6)
            thumbs = d(resourceId="com.shizhuang.duapp:id/imgThumb")
        if not thumbs.exists(timeout=1.5) or thumbs.count < 1:
            return False

        # 弹窗可能挡住缩略图，点两次：关弹窗 → 再选图
        for attempt in range(3):
            self._dismiss_camera_request_dialog(progress)
            thumbs = d(resourceId="com.shizhuang.duapp:id/imgThumb")
            if not thumbs.exists(timeout=1.0) or thumbs.count < 1:
                return False
            thumbs[0].click()
            time.sleep(1.4)
            act = d.app_current().get("activity", "") or ""
            if "PSResult" in act or "ProductDetail" in act:
                return True
            # 仍在拍照搜索：可能点到被遮罩挡住，重试
            if "GraphSearch" in act and attempt < 2:
                self._progress(
                    progress, "pick", f"选图未跳转，重试关弹窗({attempt + 1})…"
                )
                continue
            # 有时仍停在 GraphSearch 但已开始识别，交给后续等待
            return True
        return True

    def _open_first_photo_search_result(
        self, progress: ProgressCb | None = None, timeout: float = 28.0
    ) -> bool:
        """等待识图结果页并点第一个商品进入详情。"""
        d = self.d
        self._progress(progress, "result", "等待识图结果…")
        t0 = time.time()
        while time.time() - t0 < timeout:
            act = d.app_current().get("activity", "") or ""
            if "ProductDetail" in act or "product_detail" in act:
                return True
            if "PSResult" in act or d(
                resourceId="com.shizhuang.duapp:id/base_product_root"
            ).exists(timeout=0.25):
                break
            # 权限 / 摄像头弹窗
            self._dismiss_camera_request_dialog(progress)
            for t in ("允许", "始终允许", "仅在使用时允许", "同意"):
                if d(text=t).click_exists(timeout=0.2):
                    time.sleep(0.4)
            time.sleep(0.5)
        else:
            return False

        self._progress(progress, "result", "进入第一个相似商品…")
        time.sleep(0.6)
        if d(resourceId="com.shizhuang.duapp:id/base_product_img").click_exists(
            timeout=1.5
        ):
            time.sleep(1.4)
        elif d(resourceId="com.shizhuang.duapp:id/base_product_root").click_exists(
            timeout=1.0
        ):
            time.sleep(1.4)
        elif d(resourceId="com.shizhuang.duapp:id/base_product_title").click_exists(
            timeout=0.8
        ):
            time.sleep(1.4)
        else:
            w, h = d.window_size()
            d.click(int(w * 0.25), int(h * 0.40))
            time.sleep(1.4)

        return self._wait_product_detail(timeout=12.0)

    def _confirm_sku_match_dialog(self) -> bool:
        """部分商品出售前会弹「请确认实物是否一致」，点「是这款」。"""
        d = self.d
        xml = self._page_text()
        if "是这款" not in xml and "请确认实物" not in xml and "易发错货" not in xml:
            return False
        self._confirmed_articles = self._extract_article_numbers(xml)
        if d(text="是这款").click_exists(timeout=0.2):
            return self._wait_until(lambda: self._sell_sheet_open(), timeout=5)
        return False

    def _sell_sheet_open(self, xml: str | None = None) -> bool:
        xml = xml if xml is not None else self._page_text()
        return any(k in xml for k in ("预计收入", "最低出售价", "去出价", "skusView"))

    def _click_sell_toolbar_icon(self) -> bool:
        """点详情左图区右上角工具栏第一个图标（出售）。"""
        d = self.d
        # 优先：llToolBarNew 下第一个可点 ImageView
        bar = d(resourceId="com.shizhuang.duapp:id/llToolBarNew")
        if bar.exists(timeout=0.8):
            icons = bar.child(className="android.widget.ImageView", clickable=True)
            if icons.exists(timeout=0.5) and icons.count >= 1:
                icons[0].click()
                time.sleep(1.4)
                return True

        # 坐标兜底（横屏 1920x1080 常见三图标）
        w, h = d.window_size()
        for fx, fy in (
            (0.396, 0.074),
            (0.395, 0.075),
            (0.385, 0.074),
            (0.405, 0.074),
            (0.396, 0.085),
        ):
            d.click(int(w * fx), int(h * fy))
            time.sleep(1.3)
            xml = self._page_text()
            if self._sell_sheet_open(xml) or "是这款" in xml or "请确认实物" in xml:
                return True
        return False

    def _click_sell_entry(self) -> bool:
        d = self.d
        self._confirmed_articles = []
        self._income_article = None
        # 先确保回到「商品」信息区（避免停在评价 Tab）
        d(text="商品").click_exists(timeout=0.6)
        time.sleep(0.3)

        # 若已有确认弹窗，先点掉
        self._confirm_sku_match_dialog()
        if self._sell_sheet_open():
            return True

        if d(text="出售").click_exists(timeout=1.0):
            time.sleep(1.2)
            self._confirm_sku_match_dialog()
            if self._sell_sheet_open():
                return True
        if d(descriptionContains="出售").click_exists(timeout=0.6):
            time.sleep(1.2)
            self._confirm_sku_match_dialog()
            if self._sell_sheet_open():
                return True

        if self._click_sell_toolbar_icon():
            self._confirm_sku_match_dialog()
            if self._sell_sheet_open():
                return True
            # 再等一拍：确认后进出售页可能慢
            time.sleep(1.0)
            self._confirm_sku_match_dialog()
            if self._sell_sheet_open():
                return True

        return False

    def _click_estimated_income(self) -> bool:
        d = self.d
        if self._income_selected(self._page_text()):
            self._income_article = self._extract_article_number(self._page_text())
            return True
        if not d(resourceId="com.shizhuang.duapp:id/itemIncome").click_exists(timeout=0.2):
            if not d(text="预计收入").click_exists(timeout=0.2):
                return False
        selected = self._wait_until(lambda: self._income_selected(self._page_text()), timeout=5)
        if selected:
            self._income_article = self._extract_article_number(self._page_text())
        return selected

    def _income_selected(self, xml: str) -> bool:
        tabs = [
            n for n in self._xml_nodes(xml)
            if n.get("resource-id", "").endswith("/itemIncome") or n.get("text") == "预计收入"
        ]
        if tabs:
            return any(n.get("selected") == "true" for n in tabs)
        # The selected tab scrolls off-screen, but the same sheet's article
        # header stays visible. Do not lose verified state while paging sizes.
        return bool(self._income_article) and self._sku_matches(
            self._income_article, self._extract_article_number(xml)
        )

    def _size_sheet_nodes(
        self, xml: str
    ) -> tuple[list[dict], list[dict], dict | None]:
        """出售弹层内的尺码/价格节点，以及底部「去出价」按钮。"""
        sizes: list[dict] = []
        prices: list[dict] = []
        action: dict | None = None
        nodes = self._xml_nodes(xml)
        has_cells = any(n.get("resource-id", "").endswith("/tvName") for n in nodes)
        for attrs in nodes:
            text = (attrs.get("text") or "").strip()
            x1, y1, x2, y2 = (attrs[k] for k in ("x1", "y1", "x2", "y2"))
            if y1 < 160:
                continue
            node = {
                "text": text,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "cx": (x1 + x2) // 2,
                "cy": (y1 + y2) // 2,
            }
            resource = attrs.get("resource-id", "").rsplit("/", 1)[-1]
            if resource == "tvGoSell" or (text and "去出价" in text):
                if action is None or y1 > action["y1"]:
                    action = node
                continue
            if not text:
                continue
            if has_cells and resource not in ("tvName", "tvPrice"):
                continue
            size_text = self._size_from_text(text)
            if size_text:
                node["text"] = size_text
                sizes.append(node)
            elif text.startswith(("¥", "￥")) or text in ("--", "—"):
                prices.append(node)
        return sizes, prices, action

    def _size_grid_bbox(
        self, xml: str, w: int, h: int
    ) -> tuple[int, int, int, int] | None:
        sizes, prices, action = self._size_sheet_nodes(xml)
        nodes = sizes + prices
        if not nodes:
            return None
        top = min(n["y1"] for n in nodes) - 8
        bottom = max(n["y2"] for n in nodes) + 12
        left = min(n["x1"] for n in nodes) - 10
        right = max(n["x2"] for n in nodes) + 10
        if action is not None:
            bottom = min(bottom, action["y1"] - 6)
            # 网格通常接近全宽，左右可对齐出价条
            left = min(left, action["x1"])
            right = max(right, action["x2"])
        top = max(0, top)
        left = max(0, left)
        right = min(w, right)
        bottom = min(h, max(top + 40, bottom))
        if right - left < 80 or bottom - top < 40:
            return None
        return left, top, right, bottom

    def _scroll_size_sheet_to_top(self) -> None:
        """把尺码网格滚回顶部（小幅多次，避免滑出弹层）。"""
        w, h = self.d.window_size()
        for _ in range(4):
            self.d.swipe(
                int(w * 0.5), int(h * 0.52), int(w * 0.5), int(h * 0.82), 0.18
            )
            time.sleep(0.22)

    def _nudge_size_sheet_uncrop(self) -> bool:
        """
        顶行若只露出价格、尺码被裁切，则轻微下拉对齐整行。
        返回是否做了微调。
        """
        w, h = self.d.window_size()
        xml = self._page_text()
        sizes, prices, action = self._size_sheet_nodes(xml)
        if not prices:
            return False

        min_py = min(p["y1"] for p in prices)
        min_sy = min((s["y1"] for s in sizes), default=10**9)

        # 价格出现在所有尺码文字之上 → 顶行被裁
        clipped = min_py + 12 < min_sy
        if not clipped and sizes:
            # 尺码贴着出价条/网格上沿太紧也可能半截
            grid_top = min_sy
            row_h = max((s["y2"] - s["y1"] for s in sizes), default=40)
            if action and action["y1"] - grid_top < row_h * 0.55:
                clipped = True

        if not clipped:
            return False

        # 手指上滑？内容下移：起点偏低 → 终点偏高 = 下拉
        self.d.swipe(
            int(w * 0.5), int(h * 0.56), int(w * 0.5), int(h * 0.68), 0.12
        )
        time.sleep(0.28)
        return True

    def _size_sheet_page_delta(self, xml: str, h: int) -> int:
        """按当前可见网格高度估算下一屏滑动距离（约 0.82 屏，避免半行重叠过多）。"""
        sizes, _, action = self._size_sheet_nodes(xml)
        if len(sizes) >= 2:
            top = min(s["y1"] for s in sizes)
            bottom = max(s["y2"] for s in sizes)
            visible = max(bottom - top, 100)
            return max(140, min(int(h * 0.55), int(visible * 0.78)))
        if action:
            return max(80, int((action["y1"] - int(h * 0.35)) * 0.75))
        return int(h * 0.18)

    def _stitch_size_grid_shots(
        self, crops: list["Image.Image"], stem: str
    ) -> str | None:
        """把多页尺码网格竖向拼成一张。"""
        if not crops:
            return None
        try:
            from PIL import Image
        except ImportError:
            return None

        gap = 6
        width = max(im.width for im in crops)
        height = sum(im.height for im in crops) + gap * (len(crops) - 1)
        canvas = Image.new("RGB", (width, height), (247, 251, 249))
        y = 0
        for i, im in enumerate(crops):
            if im.mode != "RGB":
                im = im.convert("RGB")
            x = (width - im.width) // 2
            canvas.paste(im, (x, y))
            y += im.height
            if i < len(crops) - 1:
                # 浅分隔线
                for px in range(width):
                    canvas.putpixel((px, y + gap // 2), (210, 222, 216))
                y += gap

        path = settings.screenshot_dir / f"{stem}_sizes_{int(time.time())}.png"
        canvas.save(str(path), optimize=True)
        return path.name

    def _collect_size_prices_with_scroll(
        self, stem: str = "query"
    ) -> tuple[list[dict], str | None]:
        """
        在出售弹层滚动采集尺码，并按「整行对齐」多页截取网格后拼接。
        返回 (尺码价列表, 拼接截图文件名)。
        """
        merged: dict[str, dict] = {}
        w, h = self.d.window_size()

        xml0 = self._page_text()
        if not self._sell_sheet_open(xml0):
            return [], None

        def absorb(xml: str) -> int:
            before = len(merged)
            for row in self._extract_size_prices(xml):
                old = merged.get(row["size"])
                if old is None:
                    merged[row["size"]] = row
                elif old.get("price") is None and row.get("price") is not None:
                    merged[row["size"]] = row
                elif row.get("price") is not None:
                    merged[row["size"]] = row
            return len(merged) - before

        def sort_key(item: dict) -> tuple:
            s = item["size"]
            try:
                return (0, float(s))
            except ValueError:
                return (1, s)

        # A freshly opened sheet starts at the top. Pulling down its header
        # dismisses it on current app versions, so never scroll blindly here.
        for _ in range(3):
            if not self._nudge_size_sheet_uncrop():
                break
        fallback_shot = self._save_screenshot(f"{stem}_income")

        page_crops: list = []
        seen_signatures: set[str] = set()
        last_sig = ""
        repeated = 0
        empty_pages = 0

        try:
            from PIL import Image
        except ImportError:
            Image = None  # type: ignore

        for page in range(8):
            self._check_cancel()
            xml = self._page_text()
            if not self._income_selected(xml):
                break
            absorb(xml)

            # 对齐后再截，避免半截码
            for _ in range(2):
                if not self._nudge_size_sheet_uncrop():
                    break
            xml = self._page_text()
            absorb(xml)

            sig_sizes = tuple(
                sorted({s["text"] for s in self._size_sheet_nodes(xml)[0]})
            )
            sig = ",".join(sig_sizes)
            if not sig:
                sig = "page:" + str(page)
            if sig:
                if sig == last_sig:
                    repeated += 1
                else:
                    repeated = 0
                    last_sig = sig
            if not sig_sizes:
                empty_pages += 1
            else:
                empty_pages = 0
            if sig and sig not in seen_signatures and Image is not None:
                seen_signatures.add(sig)
                bbox = self._size_grid_bbox(xml, w, h)
                try:
                    full = self.d.screenshot(format="pillow")
                    if bbox is not None:
                        left, top, right, bottom = bbox
                        crop = full.crop((left, top, right, bottom))
                    else:
                        # 退化为中部区域
                        crop = full.crop(
                            (int(w * 0.02), int(h * 0.28), int(w * 0.98), int(h * 0.78))
                        )
                    page_crops.append(crop)
                except Exception:
                    pass

            if page >= 2 and (repeated >= 2 or empty_pages >= 2):
                break

            delta = self._size_sheet_page_delta(xml, h)
            # 上滑翻下一页（手指从下往上）
            x = int(w * 0.5)
            sizes, prices, action = self._size_sheet_nodes(xml)
            grid = sizes + prices
            if not grid:
                break
            scrolls = [
                n for n in self._xml_nodes(xml)
                if n.get("resource-id", "").endswith("/scrollView")
            ]
            y1 = min(h - 60, scrolls[0]["y2"] - 20) if scrolls else min(
                h - 60, max(n["y2"] for n in grid) - 4
            )
            if action:
                y1 = min(y1, action["y1"] - 20)
            y0 = max(int(h * 0.38), y1 - delta)
            self.d.swipe(x, y1, x, y0, 0.28)
            time.sleep(0.45)

        absorb(self._page_text())

        shot = self._stitch_size_grid_shots(page_crops, stem)
        if shot is None:
            shot = fallback_shot

        return sorted(merged.values(), key=sort_key), shot

    def _save_screenshot(self, stem: str) -> str:
        path = settings.screenshot_dir / f"{stem}_{time.time_ns()}.png"
        self.d.screenshot(str(path))
        return path.name

    def _product_image_bbox(
        self, xml: str, width: int, height: int
    ) -> tuple[int, int, int, int] | None:
        nodes = self._xml_nodes(xml)
        images = [
            n for n in nodes
            if n.get("resource-id") == "com.shizhuang.duapp:id/ivFocusImage"
            and n.get("visible-to-user") != "false"
        ]
        if not images:
            return None
        image = max(images, key=lambda n: (n["x2"] - n["x1"]) * (n["y2"] - n["y1"]))
        left, top = max(0, image["x1"]), max(0, image["y1"])
        right, bottom = min(width, image["x2"]), min(height, image["y2"])
        original_top, original_bottom = top, bottom
        for n in nodes:
            if n["x1"] >= right or n["x2"] <= left:
                continue
            resource = n.get("resource-id", "")
            if resource == "com.shizhuang.duapp:id/toolbarContainer":
                if original_top <= n["y1"] < original_top + (original_bottom - original_top) * 0.2:
                    top = max(top, n["y2"])
            elif resource == "com.shizhuang.duapp:id/itemMaskView":
                if original_top + (original_bottom - original_top) * 0.7 < n["y1"] < original_bottom:
                    bottom = min(bottom, n["y1"])
            elif resource == "com.shizhuang.duapp:id/multiDivider":
                if 0 < right - n["x1"] <= 4:
                    right = n["x1"]
        return (left, top, right, bottom) if right - left >= 120 and bottom - top >= 120 else None

    def _capture_product_image(self, xml: str, stem: str) -> str | None:
        """Capture only the observed product image; photo failure must not stop pricing."""
        try:
            from PIL import Image, ImageChops

            for attempt in range(3):
                self._check_cancel()
                full = self.d.screenshot(format="pillow").convert("RGB")
                bbox = self._product_image_bbox(xml, *full.size)
                if bbox is None:
                    return None
                crop = full.crop(bbox)
                # White product backgrounds can be trimmed without scaling the shoe.
                corners = [
                    crop.getpixel(point) for point in
                    ((0, 0), (crop.width - 1, 0), (0, crop.height - 1), (crop.width - 1, crop.height - 1))
                ]
                if all(min(pixel) >= 245 for pixel in corners):
                    diff = ImageChops.difference(crop, Image.new("RGB", crop.size, "white"))
                    mask = diff.convert("L").point(lambda value: 255 if value > 12 else 0)
                    content = mask.getbbox()
                    if content is None:
                        if attempt < 2:
                            self._sleep(0.2)
                            continue
                        return None
                    x1, y1, x2, y2 = content
                    pad = max(16, round(max(x2 - x1, y2 - y1) * 0.04))
                    crop = crop.crop((
                        max(0, x1 - pad), max(0, y1 - pad),
                        min(crop.width, x2 + pad), min(crop.height, y2 + pad),
                    ))
                path = settings.screenshot_dir / f"{stem}_product_{time.time_ns()}.png"
                crop.save(path, optimize=True)
                return path.name
        except JobCancelled:
            raise
        except Exception:
            return None
        return None

    def _save_failure_diagnostics(self, stem: str) -> str | None:
        try:
            name = f"{stem}_{time.time_ns()}"
            (settings.screenshot_dir / f"{name}.xml").write_text(
                self._page_text(), encoding="utf-8"
            )
            return self._save_screenshot(name)
        except Exception:
            return None

    def _finish_from_detail(
        self,
        query_type: str,
        query: str,
        progress: ProgressCb | None,
        t0: float,
        *,
        expected_sku: str | None = None,
        on_prices: Callable[[QueryResult], None] | None = None,
    ) -> QueryResult | None:
        """
        从详情页完成出售→预计收入→各码价格。
        若提供 expected_sku 且出售页货号不匹配，返回 None（由上层重试）。
        """
        self._progress(progress, "detail", "解析详情页售价…")
        time.sleep(0.35)
        xml = self._page_text()
        sell_price = self._extract_detail_sell_price(xml)
        title = self._guess_title(xml)
        stem = re.sub(r"[^\w\-]+", "_", query)[:40] or "query"
        product_image = self._capture_product_image(xml, stem)

        self._progress(progress, "sell", "点击出售…")
        if not self._click_sell_entry():
            shot = self._save_screenshot("detail_no_sell")
            return QueryResult(
                ok=False,
                query_type=query_type,
                query=query,
                title=title,
                sell_price=sell_price,
                screenshot=shot,
                product_image=product_image,
                message=(
                    f"已读到售价 ¥{sell_price}，但未能打开出售页"
                    if sell_price
                    else "未能打开出售页"
                ),
                elapsed_ms=int((time.time() - t0) * 1000),
            )

        # 出售页货号校验
        xml_sell = self._page_text()
        article = self._extract_article_number(xml_sell)
        if expected_sku:
            if not self._verify_sku_on_sell_sheet(expected_sku, xml_sell):
                self._progress(
                    progress,
                    "sku_mismatch",
                    f"货号不匹配：页面={article or '未读到'}，查询={expected_sku}，将返回重试",
                )
                return None

        self._progress(progress, "income", "切换到预计收入…")
        if not self._click_estimated_income():
            shot = self._save_screenshot("no_income_sheet")
            return QueryResult(
                ok=False,
                query_type=query_type,
                query=query,
                title=title,
                sell_price=sell_price,
                article_number=article,
                screenshot=shot,
                product_image=product_image,
                message="未能确认已选中预计收入，未采集尺码价",
                elapsed_ms=int((time.time() - t0) * 1000),
            )

        self._progress(progress, "sizes", "读取各码预计收入并截取尺码页…")
        size_prices, shot = self._collect_size_prices_with_scroll(stem=stem)
        xml2 = self._page_text()
        title = self._guess_title(xml2) or title
        article = self._extract_article_number(xml2) or article
        valid = [r["price"] for r in size_prices if r.get("price") is not None]
        lowest = min(valid) if valid else None
        if not shot:
            shot = self._save_screenshot(stem)

        msg_parts = []
        if sell_price is not None:
            msg_parts.append(f"售价 ¥{sell_price}")
        if size_prices:
            msg_parts.append(f"尺码 {len(size_prices)} 个")
        if valid:
            msg_parts.append(f"有价 {len(valid)} 个")
        elif size_prices:
            msg_parts.append("尺码价多为空，请确认已在预计收入")
        if article:
            msg_parts.append(f"货号 {article}")
        result = QueryResult(
            ok=bool(valid),
            query_type=query_type,
            query=query,
            title=title,
            sell_price=sell_price,
            lowest_price=lowest,
            prices=valid[:20],
            size_prices=size_prices,
            article_number=article,
            article_numbers=list(dict.fromkeys(
                ([article] if article else []) + self._confirmed_articles
            )),
            screenshot=shot,
            product_image=product_image,
            message=" · ".join(msg_parts) if msg_parts else "未解析到价格",
            elapsed_ms=int((time.time() - t0) * 1000),
        )
        if not result.ok:
            return result
        if on_prices:
            on_prices(result)
        # Recent-purchase samples are not official seven-day sales. Query only
        # merchant sales here, using the verified primary article number.
        try:
            merchant_shot, merchant_sales, _ = self._capture_merchant_sales(
                (article or expected_sku or "").strip(), progress, stem=stem
            )
            result.merchant_screenshot = merchant_shot
            result.sales_by_size = merchant_sales
            result.sales_source = "merchant_7d" if merchant_sales else None
            if merchant_sales:
                msg_parts.append(f"商家7日销量{len(merchant_sales)}码")
            else:
                result.warnings.append("商家销量未采集，不能据此判断是否值得买")
        except JobCancelled:
            result.warnings.append("已终止销量采集，保留已核对的预计收入")
        except Exception as exc:
            result.warnings.append(f"销量采集失败：{exc}")
        result.message = " · ".join(msg_parts + result.warnings)
        result.elapsed_ms = int((time.time() - t0) * 1000)
        return result

    def query_by_sku(
        self, sku: str, progress: ProgressCb | None = None,
        on_prices: Callable[[QueryResult], None] | None = None,
    ) -> QueryResult:
        t0 = time.time()
        sku = (sku or "").strip()
        if not sku:
            return QueryResult(False, "sku", sku, message="货号不能为空")

        max_attempts = 3
        self._begin_job()
        try:
            self._progress(progress, "connect", "连接模拟器…")
            self.connect()
            self._progress(progress, "open", "打开得物…")
            self._open_dewu()

            for attempt in range(1, max_attempts + 1):
                self._progress(
                    progress,
                    "attempt",
                    f"第 {attempt}/{max_attempts} 次查询 {sku}",
                )
                if attempt > 1:
                    # 货号不符：只退回搜索列表换下一条，不回首页、不重搜
                    self._progress(
                        progress, "retry", "退回搜索列表，打开下一条结果…"
                    )
                    if not self._recover_to_search_results():
                        self._progress(
                            progress, "retry", "未能回到搜索列表，重新搜索…"
                        )
                        self._prepare_sku_search(sku, progress)
                else:
                    # 首次：能回到搜索层就直搜，不必先刷推荐首页
                    self._prepare_sku_search(sku, progress)

                self._progress(
                    progress, "open_result", f"打开第 {attempt} 个搜索结果…"
                )
                self._open_result_at(attempt - 1)

                result = self._finish_from_detail(
                    "sku",
                    sku,
                    progress,
                    t0,
                    expected_sku=sku,
                    on_prices=on_prices,
                )
                if result is not None:
                    return result

            shot = self._save_screenshot("not_found")
            self._close_sell_sheet()
            return QueryResult(
                ok=False,
                query_type="sku",
                query=sku,
                screenshot=shot,
                message="未找到",
                elapsed_ms=int((time.time() - t0) * 1000),
            )
        except JobCancelled as e:
            return QueryResult(
                ok=False,
                query_type="sku",
                query=sku,
                message=str(e) or "用户已终止查询",
                elapsed_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return QueryResult(
                ok=False,
                query_type="sku",
                query=sku,
                message=f"查询失败: {e}",
                screenshot=self._save_failure_diagnostics("sku_failure"),
                elapsed_ms=int((time.time() - t0) * 1000),
            )
        finally:
            self._end_job()

    def query_by_image(
        self, image_path: Path, progress: ProgressCb | None = None,
        on_prices: Callable[[QueryResult], None] | None = None,
    ) -> QueryResult:
        t0 = time.time()
        image_path = Path(image_path)
        if not image_path.exists():
            return QueryResult(
                False, "image", str(image_path), message="图片不存在"
            )

        self._begin_job()
        try:
            self._progress(progress, "connect", "连接模拟器…")
            self.connect()
            self._progress(progress, "open", "打开得物…")
            self._open_dewu()
            self._go_home(progress)

            # 推到相册目录，便于「我的相册」立刻看到
            remote_pic = "/sdcard/Pictures/dewu_query.jpg"
            remote_dcim = "/sdcard/DCIM/Camera/dewu_query.jpg"
            self._progress(progress, "push", "推送图片到模拟器相册…")
            self.d.push(str(image_path), remote_pic)
            try:
                self.d.shell(f"cp {remote_pic} {remote_dcim}")
            except Exception:
                try:
                    self.d.push(str(image_path), remote_dcim)
                except Exception:
                    pass
            for path in (remote_pic, remote_dcim):
                try:
                    self.d.shell(
                        "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                        f"-d file://{path}"
                    )
                except Exception:
                    pass
            self._sleep(0.8)

            if not self._open_photo_search(progress):
                shot = self._save_screenshot("image_no_camera")
                return QueryResult(
                    ok=False,
                    query_type="image",
                    query=image_path.name,
                    screenshot=shot,
                    message="未能打开拍照搜索（请确认首页搜索栏旁相机图标）",
                    elapsed_ms=int((time.time() - t0) * 1000),
                )

            if not self._pick_album_image_for_search(progress):
                shot = self._save_screenshot("image_no_thumb")
                return QueryResult(
                    ok=False,
                    query_type="image",
                    query=image_path.name,
                    screenshot=shot,
                    message="相册中未找到可点选的图片，请确认已授权相册权限",
                    elapsed_ms=int((time.time() - t0) * 1000),
                )

            if not self._open_first_photo_search_result(progress):
                shot = self._save_screenshot("image_timeout")
                return QueryResult(
                    ok=False,
                    query_type="image",
                    query=image_path.name,
                    screenshot=shot,
                    message="识图结果超时或未能进入商品详情",
                    elapsed_ms=int((time.time() - t0) * 1000),
                )

            return self._finish_from_detail(
                "image", image_path.name, progress, t0, on_prices=on_prices
            )
        except JobCancelled as e:
            return QueryResult(
                ok=False,
                query_type="image",
                query=image_path.name,
                message=str(e) or "用户已终止查询",
                elapsed_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return QueryResult(
                ok=False,
                query_type="image",
                query=image_path.name,
                message=f"查询失败: {e}",
                screenshot=self._save_failure_diagnostics("image_failure"),
                elapsed_ms=int((time.time() - t0) * 1000),
            )
        finally:
            self._end_job()


bot = DewuBot()
