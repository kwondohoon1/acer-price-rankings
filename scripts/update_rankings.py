from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote_plus, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

HEADERS = [
    "날짜",
    "마켓",
    "순위",
    "판매처",
    "상품명(모델명)",
    "제조사(브랜드)",
    "CPU",
    "VGA",
    "메모리",
    "SSD",
    "OS",
    "디스플레이",
    "정상가",
    "판매가",
    "혜택가",
    "쿠폰/카드혜택",
    "URL",
]

GMARKET_DEFAULT_URL = (
    "https://www.gmarket.co.kr/n/list?"
    "spm=gmktpc.categorylist.0.0.71246e67JJKfgW&category=200001966"
)

FOREIGN_BRANDS = {
    "Acer",
    "Apple",
    "ASUS",
    "Basics",
    "Chuwi",
    "Dell",
    "Dynabook",
    "Gigabyte",
    "HP",
    "Huawei",
    "Lenovo",
    "Microsoft",
    "MSI",
    "Razer",
    "Xiaomi",
}

DOMESTIC_BRANDS = {"LG전자", "삼성전자", "Samsung", "LG"}

BRAND_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("삼성전자", ("삼성", "갤럭시북", "Samsung")),
    ("LG전자", ("LG전자", "LG그램", "LG 그램", "울트라PC", "LG")),
    ("Lenovo", ("레노버", "Lenovo", "ThinkPad", "IdeaPad", "LOQ", "Yoga")),
    ("HP", ("HP", "Victus", "Pavilion", "EliteBook", "ProBook", "OMEN")),
    ("ASUS", ("ASUS", "에이수스", "젠북", "비보북", "TUF", "ROG")),
    ("Acer", ("Acer", "에이서", "스위프트", "Swift", "Aspire", "Nitro")),
    ("MSI", ("MSI", "엠에스아이", "스텔스", "프레스티지", "모던")),
    ("Apple", ("Apple", "애플", "MacBook", "맥북")),
    ("Dell", ("Dell", "델", "Inspiron", "XPS", "Latitude")),
    ("Microsoft", ("Microsoft", "마이크로소프트", "Surface", "서피스")),
    ("Gigabyte", ("Gigabyte", "기가바이트", "AORUS")),
    ("Basics", ("베이직스", "Basics", "베이직북")),
    ("Chuwi", ("Chuwi", "추위")),
    ("Xiaomi", ("Xiaomi", "샤오미")),
]


@dataclass
class ProductRow:
    date: str
    market: str
    rank: int
    seller: str
    title: str
    brand: str
    cpu: str
    vga: str
    memory: str
    ssd: str
    os: str
    display: str
    list_price: str
    sale_price: str
    benefit_price: str
    benefits: str
    url: str

    def csv_row(self) -> dict[str, Any]:
        return {
            "날짜": self.date,
            "마켓": self.market,
            "순위": self.rank,
            "판매처": self.seller,
            "상품명(모델명)": self.title,
            "제조사(브랜드)": self.brand,
            "CPU": self.cpu,
            "VGA": self.vga,
            "메모리": self.memory,
            "SSD": self.ssd,
            "OS": self.os,
            "디스플레이": self.display,
            "정상가": self.list_price,
            "판매가": self.sale_price,
            "혜택가": self.benefit_price,
            "쿠폰/카드혜택": self.benefits,
            "URL": self.url,
        }


class CollectorError(RuntimeError):
    pass


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    return client


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def only_digits(value: Any) -> str:
    text = clean_text(value)
    digits = re.sub(r"[^\d]", "", text)
    return digits


def first_present(*values: Any) -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def infer_brand(title: str, fallback: str = "") -> str:
    fallback = clean_text(fallback)
    if fallback:
        for canonical, patterns in BRAND_PATTERNS:
            if any(pattern.lower() in fallback.lower() for pattern in patterns):
                return canonical
        return fallback

    normalized = title.lower()
    for canonical, patterns in BRAND_PATTERNS:
        if any(pattern.lower() in normalized for pattern in patterns):
            return canonical
    return ""


def infer_specs(title: str, brand: str = "") -> dict[str, str]:
    text = clean_text(title)
    upper = text.upper()

    cpu_patterns = [
        r"(ULTRA\s*[3579]\s*[- ]?\d{3,4}[A-Z]*)",
        r"(CORE\s*I[3579]\s*[- ]?\d{4,5}[A-Z]*)",
        r"\b(I[3579]\s*[- ]?\d{4,5}[A-Z]*)\b",
        r"\b(I[3579])\b",
        r"(RYZEN\s*[3579]\s*[- ]?\d{4,5}[A-Z]*)",
        r"(RYZEN\s*[3579])",
        r"(라이젠\s*[3579])",
        r"(SNAPDRAGON\s*(?:X|[0-9A-Z]+\+?)(?:\s*GEN\s*\d+)?)",
        r"(스냅드래곤\s*(?:X|[0-9A-Z]+\+?)(?:\s*GEN\s*\d+)?)",
        r"(INTEL\s*PROCESSOR\s*[A-Z0-9 ]{1,12})",
        r"\b(N[0-9]{2,4})\b",
    ]
    cpu = find_regex(upper, cpu_patterns)

    if re.search(r"\bRTX\s?\d{4}\b", upper):
        vga = find_regex(upper, [r"\bRTX\s?\d{4}\b"])
    elif re.search(r"\bGTX\s?\d{4}\b", upper):
        vga = find_regex(upper, [r"\bGTX\s?\d{4}\b"])
    elif "RADEON" in upper:
        vga = "Radeon"
    elif "ARC" in upper:
        vga = "Intel Arc"
    else:
        vga = "integrated"

    memory = find_regex(upper, [r"(?:RAM|메모리)?\s*(\d{1,3}\s?GB)\s*(?:RAM|메모리)?"])
    storage_candidates = re.findall(r"(\d+(?:\.\d+)?\s?(?:TB|GB))", upper)
    ssd = ""
    for candidate in storage_candidates:
        compact = candidate.replace(" ", "")
        if compact != memory.replace(" ", ""):
            number = float(re.match(r"\d+(?:\.\d+)?", compact).group(0))  # type: ignore[union-attr]
            if "TB" in compact or number >= 64:
                ssd = compact
                break

    if "WIN" in upper or "WINDOW" in upper:
        os_value = "WIN 11 HOME" if "11" in upper else "Windows"
    elif "FREEDOS" in upper or "FREE DOS" in upper or "미포함" in text:
        os_value = "FreeDOS"
    elif "ESHELL" in upper:
        os_value = "Eshell"
    else:
        os_value = ""

    display = find_regex(
        text,
        [
            r"(\d{2}(?:\.\d)?\s?(?:인치|inch|\"))",
            r"(\d{2}(?:\.\d)?\s?(?:형))",
        ],
    )
    resolution = find_regex(upper, [r"(\d{3,4}\s?[Xx]\s?\d{3,4})"])
    if display and resolution:
        display = f"{display} {resolution.upper().replace(' ', '')}"

    return {
        "brand": infer_brand(text, brand),
        "cpu": cpu,
        "vga": vga,
        "memory": memory.replace(" ", ""),
        "ssd": ssd,
        "os": os_value,
        "display": display,
    }


def find_regex(text: str, patterns: Iterable[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            group = match.group(1) if match.groups() else match.group(0)
            return clean_text(group).replace("CORE ", "")
    return ""


def make_row(
    *,
    date: str,
    market: str,
    rank: int,
    seller: str,
    title: str,
    brand: str = "",
    list_price: Any = "",
    sale_price: Any = "",
    benefit_price: Any = "",
    benefits: Any = "",
    url: str = "",
) -> ProductRow:
    specs = infer_specs(title, brand)
    sale = only_digits(sale_price)
    benefit = only_digits(benefit_price) or sale
    normal = only_digits(list_price)
    return ProductRow(
        date=date,
        market=market,
        rank=rank,
        seller=clean_text(seller),
        title=clean_text(title),
        brand=specs["brand"],
        cpu=specs["cpu"],
        vga=specs["vga"],
        memory=specs["memory"],
        ssd=specs["ssd"],
        os=specs["os"],
        display=specs["display"],
        list_price=normal,
        sale_price=sale,
        benefit_price=benefit,
        benefits=clean_text(benefits),
        url=clean_text(url),
    )


def fetch_naver_items(
    query: str,
    max_items: int,
    client_id: str,
    client_secret: str,
    accept: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    if not client_id or not client_secret:
        raise CollectorError("NAVER_CLIENT_ID and NAVER_CLIENT_SECRET are required")

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    start = 1
    scan_limit = int(os.getenv("NAVER_SCAN_LIMIT", "1000"))
    client = session()

    while len(items) < max_items and start <= scan_limit:
        display = min(100, scan_limit - start + 1)
        response = client.get(
            "https://openapi.naver.com/v1/search/shop.json",
            headers=headers,
            params={"query": query, "display": display, "start": start, "sort": "sim"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        page_items = payload.get("items", [])
        if not page_items:
            break

        for item in page_items:
            if accept and not accept(item):
                continue
            key = first_present(item.get("productId"), item.get("link"), item.get("title"))
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= max_items:
                break

        start += len(page_items)

    return items


def naver_item_to_row(item: dict[str, Any], date: str, market: str, rank: int) -> ProductRow:
    title = clean_text(item.get("title"))
    price = only_digits(item.get("lprice"))
    high_price = only_digits(item.get("hprice"))
    brand = first_present(item.get("maker"), item.get("brand"))
    seller = first_present(item.get("mallName"), market)
    return make_row(
        date=date,
        market=market,
        rank=rank,
        seller=seller,
        title=title,
        brand=brand,
        list_price=high_price if high_price and high_price != price else "",
        sale_price=price,
        benefit_price=price,
        benefits="",
        url=first_present(item.get("link")),
    )


def collect_naver(date: str, query: str, max_items: int, client_id: str, client_secret: str) -> list[ProductRow]:
    items = fetch_naver_items(query, max_items, client_id, client_secret)
    return [naver_item_to_row(item, date, "네이버", idx) for idx, item in enumerate(items, 1)]


def collect_market_via_naver(
    date: str,
    market: str,
    query: str,
    max_items: int,
    client_id: str,
    client_secret: str,
) -> list[ProductRow]:
    if market == "쿠팡":
        keywords = ("쿠팡", "Coupang")
        host = "coupang.com"
        fallback_query = f"쿠팡 {query}"
    elif market == "지마켓":
        keywords = ("지마켓", "G마켓", "Gmarket")
        host = "gmarket.co.kr"
        fallback_query = f"지마켓 {query}"
    else:
        raise ValueError(f"Unsupported fallback market: {market}")

    def accept(item: dict[str, Any]) -> bool:
        mall_name = clean_text(item.get("mallName")).lower()
        link = clean_text(item.get("link")).lower()
        return any(keyword.lower() in mall_name for keyword in keywords) or host in link

    items = fetch_naver_items(fallback_query, max_items, client_id, client_secret, accept=accept)
    return [naver_item_to_row(item, date, market, idx) for idx, item in enumerate(items, 1)]


def collect_coupang_store(date: str, max_items: int) -> list[ProductRow]:
    vendor_id = os.getenv("COUPANG_VENDOR_ID", "").strip()
    if not vendor_id:
        raise CollectorError("COUPANG_VENDOR_ID is not set")

    client = session()
    client.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    rows: list[ProductRow] = []
    page = 1
    page_size = min(max_items, 100)

    while len(rows) < max_items:
        response = client.post(
            "https://shop.coupang.com/api/v1/listing",
            json={"vendorId": vendor_id, "page": page, "size": page_size},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) not in {"200", "SUCCESS"} and payload.get("code") != 200:
            raise CollectorError(f"Coupang listing failed: {payload.get('msg') or payload.get('message')}")

        products = payload.get("data", {}).get("products", [])
        if not products:
            break

        for product in products:
            title = first_deep(product, ("title", "productName", "itemName"))
            price = product.get("priceArea", {}) if isinstance(product.get("priceArea"), dict) else {}
            image_title = product.get("imageAndTitleArea", {})
            if isinstance(image_title, dict):
                title = first_present(title, image_title.get("title"))
            url = first_deep(product, ("productUrl", "url", "link", "detailUrl"))
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = urljoin("https://www.coupang.com", url)

            rows.append(
                make_row(
                    date=date,
                    market="쿠팡",
                    rank=len(rows) + 1,
                    seller=first_deep(product, ("vendorName", "sellerName", "shopName")),
                    title=title,
                    brand=first_deep(product, ("brand", "brandName", "maker")),
                    list_price=first_present(price.get("originalPrice"), price.get("basePrice")),
                    sale_price=first_present(price.get("salesPrice"), price.get("price")),
                    benefit_price=first_present(price.get("lowestPrice"), price.get("finalPrice")),
                    benefits=first_deep(product, ("discountDescription", "couponDescription")),
                    url=url,
                )
            )
            if len(rows) >= max_items:
                break

        page += 1

    return rows


def first_deep(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and clean_text(payload[key]):
                return clean_text(payload[key])
        for value in payload.values():
            found = first_deep(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = first_deep(value, keys)
            if found:
                return found
    return ""


def collect_gmarket_direct(date: str, max_items: int) -> list[ProductRow]:
    url = os.getenv("GMARKET_CATEGORY_URL", GMARKET_DEFAULT_URL)
    client = session()
    response = client.get(url, timeout=25)
    response.raise_for_status()
    if "cf-mitigated" in response.headers or "Just a moment" in response.text:
        raise CollectorError("Gmarket returned a Cloudflare challenge")

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = soup.select(
        "div.box__item-container, div.box__component, li.box__item, li[class*=item], div[class*=item]"
    )
    rows: list[ProductRow] = []
    seen_urls: set[str] = set()

    for element in candidates:
        link = element.select_one("a[href*='goodscode'], a[href*='Item']")
        if not link:
            continue
        href = link.get("href", "")
        if not href:
            continue
        product_url = urljoin("https://www.gmarket.co.kr", href)
        if product_url in seen_urls:
            continue
        seen_urls.add(product_url)

        title_node = element.select_one(
            ".text__item, .link__item, .itemname, .text__title, a[href*='goodscode']"
        )
        price_node = element.select_one(".text__value, .price_real, .box__price-seller strong")
        seller_node = element.select_one(".text__seller, .seller, .box__seller")
        coupon_node = element.select_one(".box__discount, .text__coupon, .box__benefit")

        title = clean_text(title_node.get_text(" ")) if title_node else clean_text(link.get_text(" "))
        if not title:
            continue

        rows.append(
            make_row(
                date=date,
                market="지마켓",
                rank=len(rows) + 1,
                seller=clean_text(seller_node.get_text(" ")) if seller_node else "Gmarket",
                title=title,
                sale_price=clean_text(price_node.get_text(" ")) if price_node else "",
                benefit_price=clean_text(price_node.get_text(" ")) if price_node else "",
                benefits=clean_text(coupon_node.get_text(" ")) if coupon_node else "",
                url=product_url,
            )
        )
        if len(rows) >= max_items:
            break

    if not rows:
        raise CollectorError("No Gmarket products parsed from category page")
    return rows


def collect_with_fallbacks(
    date: str,
    query: str,
    max_items: int,
    client_id: str,
    client_secret: str,
) -> tuple[list[ProductRow], dict[str, Any]]:
    summary: dict[str, Any] = {
        "date": date,
        "query": query,
        "max_items_per_market": max_items,
        "markets": {},
        "warnings": [],
    }
    all_rows: list[ProductRow] = []

    collectors: list[tuple[str, Callable[[], list[ProductRow]], Callable[[], list[ProductRow]] | None]] = [
        (
            "쿠팡",
            lambda: collect_coupang_store(date, max_items),
            lambda: collect_market_via_naver(date, "쿠팡", query, max_items, client_id, client_secret),
        ),
        (
            "지마켓",
            lambda: collect_gmarket_direct(date, max_items),
            lambda: collect_market_via_naver(date, "지마켓", query, max_items, client_id, client_secret),
        ),
        (
            "네이버",
            lambda: collect_naver(date, query, max_items, client_id, client_secret),
            None,
        ),
    ]

    for market, primary, fallback in collectors:
        method = "primary"
        try:
            rows = primary()
        except Exception as exc:
            if fallback is None:
                summary["markets"][market] = {"count": 0, "method": method, "error": str(exc)}
                summary["warnings"].append(f"{market}: {exc}")
                continue
            method = "naver_fallback"
            summary["warnings"].append(f"{market} primary collector failed; used Naver fallback: {exc}")
            rows = fallback()

        for idx, row in enumerate(rows[:max_items], 1):
            row.rank = idx
        summary["markets"][market] = {"count": len(rows[:max_items]), "method": method}
        all_rows.extend(rows[:max_items])

    return all_rows, summary


def is_foreign(row: ProductRow) -> bool:
    if row.brand in DOMESTIC_BRANDS:
        return False
    if row.brand in FOREIGN_BRANDS:
        return True
    title = row.title.lower()
    return any(brand.lower() in title for brand in FOREIGN_BRANDS)


def top5(rows: list[ProductRow]) -> list[ProductRow]:
    return [row for row in rows if row.rank <= 5]


def write_csv(path: Path, rows: list[ProductRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.csv_row())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def copy_latest(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def main() -> None:
    now = datetime.now(KST)
    date = now.strftime("%Y-%m-%d")
    query = os.getenv("SHOPPING_QUERY", "노트북").strip() or "노트북"
    max_items = max(1, min(100, int(os.getenv("MAX_ITEMS", "100"))))
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()

    rows, summary = collect_with_fallbacks(date, query, max_items, client_id, client_secret)
    foreign_rows = [row for row in rows if is_foreign(row)]
    top_rows = top5(rows)

    dated_dir = DATA_DIR / date
    rankings_path = dated_dir / "rankings.csv"
    foreign_path = dated_dir / "foreign_rankings.csv"
    top5_path = dated_dir / "top5.csv"
    summary_path = dated_dir / "run_summary.json"

    summary.update(
        {
            "generated_at": now.isoformat(),
            "total_rows": len(rows),
            "foreign_rows": len(foreign_rows),
            "top5_rows": len(top_rows),
        }
    )

    write_csv(rankings_path, rows)
    write_csv(foreign_path, foreign_rows)
    write_csv(top5_path, top_rows)
    write_json(summary_path, summary)

    copy_latest(rankings_path, DATA_DIR / "rankings_latest.csv")
    copy_latest(foreign_path, DATA_DIR / "foreign_rankings_latest.csv")
    copy_latest(top5_path, DATA_DIR / "top5_latest.csv")
    copy_latest(summary_path, DATA_DIR / "run_summary_latest.json")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
