"""
당근마켓 리스트 페이지 크롤링 → CSV 저장 (상세 페이지 진입 없음)

[동작 흐름]
  1. main() → 터미널 인자 파싱, daangn_urls.txt 에서 URL 목록 읽기
  2. asyncio.run(run(...)) → 비동기 진입점
  3. run() → Playwright로 브라우저 실행 → 새 탭에서 각 URL마다 scrape_list_page_items() 호출
  4. scrape_list_page_items() → 페이지 로드 → 스크롤 → 카드 개수만큼 루프로 제목/가격/동네/시간 추출
  5. run() 끝에서 수집한 행들을 CSV로 저장

[수집 방식]
  - 페이지 로드 후 카드는 이미 DOM에 있으므로 카드별 긴 대기 없이 짧은 타임아웃(2초)으로만 읽음.
  - "더보기" 버튼(//*[@id="main-content"]/div[1]/div/section/div/div[3]/button) 클릭으로 추가 카드 로딩 후, 새로 붙은 카드만 이어서 수집.
  - 수집 필드: 상품명(title), 상품설명(description, 리스트에 있으면), 가격(price), 동네(neighborhood), 시간(time_text).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin


DAANGN_ORIGIN = "https://www.daangn.com"

# ---------- XPath 설정 (당근 리스트 페이지 DOM 구조에 맞춤) ----------
# 사용자 제공 XPath(첫 번째 카드의 "상품명" 위치):
# //*[@id="main-content"]/div[1]/div/section/div/div[2]/a[1]/div/div[2]/div[1]
# "여러 카드"를 잡기 위해 a[1]을 a 로 바꾼 XPath → 상품 카드 링크(<a>) 전부 선택
DEFAULT_LIST_CARD_ANCHORS_XPATH = '//*[@id="main-content"]/div[1]/div/section/div/div[2]/a'
# 각 카드(<a>) 안에서: div > div[2] 가 정보 wrapper, 그 안에 제목/가격/동네/시간
DEFAULT_LIST_INFO_WRAPPER_REL_XPATH = "./div/div[2]"
DEFAULT_LIST_TITLE_REL_XPATH = "./div/div[2]/div[1]/div[1]"
DEFAULT_LIST_PRICE_REL_XPATH = "./div/div[2]/div[1]/div[2]"
DEFAULT_LIST_NEIGHBORHOOD_REL_XPATH = "./div/div[2]/div[2]/div[1]"
DEFAULT_LIST_TIME_REL_XPATH = ".//time"
# 리스트 카드에 상품설명(한 줄 요약)이 있으면 그 요소 XPath. 없으면 빈 문자열로 둠.
DEFAULT_LIST_DESCRIPTION_REL_XPATH = ""  # 당근 리스트에는 보통 없음. 필요 시 예: ".//div[@class='desc']"

# "더보기" 버튼: 클릭 시 추가 카드 로딩
DEFAULT_LOAD_MORE_BUTTON_XPATH = '//*[@id="main-content"]/div[1]/div/section/div/div[3]/button'
# 더보기 클릭 후 새 카드가 붙을 때까지 대기(ms)
LOAD_MORE_WAIT_MS = 1800


# ---------- CSV 한 행에 들어갈 데이터 ----------
@dataclass(frozen=True)
class ListingRow:
    source_list_url: str   # 크롤링한 리스트 페이지 URL (예: 둔산동 검색 결과)
    item_url: str          # 상품 상세 페이지 링크 (상세는 안 들어감)
    title: str             # 상품명
    description: str       # 상품설명 (리스트에 있으면 수집, 없으면 "")
    price: str
    neighborhood: str
    time_text: str         # "5분 전", "끌올 1시간 전" 등
    scraped_at_utc: str    # 수집 시각 (UTC)


def _read_urls(path: str) -> list[str]:
    """daangn_urls.txt 같은 파일에서 URL 한 줄씩 읽기. 빈 줄·# 시작 줄은 무시."""
    urls: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            urls.append(s)
    return urls


def _ensure_parent_dir(file_path: str) -> None:
    """CSV 저장 경로의 상위 폴더가 없으면 생성 (예: out/daangn/)."""
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _utc_now_iso() -> str:
    """현재 시각을 UTC 기준 ISO 문자열로 (scraped_at_utc 용)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _absolutize_daangn_url(href: str | None) -> str | None:
    """상대 경로(/kr/buy-sell/...)를 당근 전체 URL로 변환. None/빈 값이면 None."""
    if not href:
        return None
    href = href.strip()
    if not href:
        return None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return urljoin(DAANGN_ORIGIN, href)
    return urljoin(DAANGN_ORIGIN + "/", href)


async def _auto_scroll(page, scroll_count: int, scroll_wait_ms: int) -> None:
    """페이지 맨 아래까지 스크롤을 scroll_count 번 반복. 무한 스크롤로 더 많은 카드 로딩용."""
    for _ in range(max(0, scroll_count)):
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(max(0, scroll_wait_ms))


async def _safe_inner_text(locator, timeout_ms: int) -> str:
    """요소의 보이는 텍스트 추출. 없거나 타임아웃이면 빈 문자열. (한 요소당 최대 timeout_ms 대기)"""
    try:
        return (await locator.first.inner_text(timeout=timeout_ms)).strip()
    except Exception:
        return ""


async def _safe_get_attribute(locator, name: str, timeout_ms: int) -> str:
    """요소의 속성(예: href) 추출. 없거나 타임아웃이면 빈 문자열."""
    try:
        v = await locator.first.get_attribute(name, timeout=timeout_ms)
        return (v or "").strip()
    except Exception:
        return ""


# 카드는 이미 로드된 DOM에 있으므로 요소 조회는 짧게만 대기 (페이지 로드 후 별도 대기 불필요).
ELEMENT_TIMEOUT_MS = 2000


async def scrape_list_page_items(
    page,
    list_url: str,
    *,
    anchors_xpath: str,
    info_wrapper_rel_xpath: str,
    title_rel_xpath: str,
    price_rel_xpath: str,
    neighborhood_rel_xpath: str,
    time_rel_xpath: str,
    description_rel_xpath: str,
    load_more_button_xpath: str,
    limit: int,
    scroll_count: int,
    scroll_wait_ms: int,
    timeout_ms: int,
    verbose: bool = True,
) -> list[tuple[str, str, str, str, str, str]]:
    """
    당근 리스트 페이지에서 상품 카드 수집. 상세 페이지는 안 들어감.
    - 페이지 로드 후 카드는 이미 DOM에 있으므로 카드별 긴 대기 없이 빠르게 읽음.
    - "더보기" 버튼을 눌러 추가 카드를 로딩하고, 새로 붙은 카드만 이어서 수집.
    반환: (item_url, title, description, price, neighborhood, time_text) 튜플 리스트.
    """
    # --- 1단계: 페이지 로드 ---
    if verbose:
        print(f"  Loading: {list_url[:60]}...")
    await page.goto(list_url, wait_until="networkidle", timeout=timeout_ms)

    # --- 2단계: (선택) 초기 스크롤로 일부 추가 로딩 ---
    for s in range(max(0, scroll_count)):
        if verbose and scroll_count > 0:
            print(f"  Scroll {s + 1}/{scroll_count}...")
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(max(0, scroll_wait_ms))

    elem_timeout = min(ELEMENT_TIMEOUT_MS, timeout_ms)
    results: list[tuple[str, str, str, str, str, str]] = []
    processed_count = 0  # 이미 수집한 카드 개수 (더보기 후 새로 붙은 것만 수집하기 위함)

    # --- 3단계: "더보기" 반복 — 현재 보이는 카드 수집 → 더보기 클릭 → 새 카드만 추가 수집 ---
    while True:
        anchors = page.locator(f"xpath={anchors_xpath}")
        count = await anchors.count()
        # 이번에 수집할 구간: processed_count ~ min(count, limit)
        end = count if limit <= 0 else min(count, limit)
        if verbose and (processed_count > 0 or end > 0):
            print(f"  Cards {processed_count + 1}~{end} (total visible: {count})...")

        for i in range(processed_count, end):
            a = anchors.nth(i)
            href = await _safe_get_attribute(a, "href", timeout_ms=elem_timeout)
            item_url = _absolutize_daangn_url(href)
            if not item_url:
                continue

            # DOM이 이미 있으므로 짧은 타임아웃으로만 읽음 (카드별 추가 대기 없음)
            title = await _safe_inner_text(a.locator(f"xpath={title_rel_xpath}"), timeout_ms=elem_timeout)
            price = await _safe_inner_text(a.locator(f"xpath={price_rel_xpath}"), timeout_ms=elem_timeout)
            neighborhood = await _safe_inner_text(
                a.locator(f"xpath={neighborhood_rel_xpath}"), timeout_ms=elem_timeout
            )
            time_text = await _safe_inner_text(a.locator(f"xpath={time_rel_xpath}"), timeout_ms=elem_timeout)
            description = ""
            if description_rel_xpath:
                description = await _safe_inner_text(
                    a.locator(f"xpath={description_rel_xpath}"), timeout_ms=elem_timeout
                )

            if not (title or price or neighborhood):
                info = a.locator(f"xpath={info_wrapper_rel_xpath}")
                title = await _safe_inner_text(info.locator("xpath=.//div[1]/div[1]"), timeout_ms=elem_timeout)
                price = await _safe_inner_text(info.locator("xpath=.//div[1]/div[2]"), timeout_ms=elem_timeout)
                neighborhood = await _safe_inner_text(info.locator("xpath=.//div[2]/div[1]"), timeout_ms=elem_timeout)
                if not time_text:
                    time_text = await _safe_inner_text(info.locator("xpath=.//time"), timeout_ms=elem_timeout)

            results.append((item_url, title, description, price, neighborhood, time_text))

        processed_count = end  # 이번에 수집한 끝 인덱스
        if limit > 0 and processed_count >= limit:
            break

        # --- 더보기 버튼 클릭 (있으면 추가 카드 로딩) ---
        load_more = page.locator(f"xpath={load_more_button_xpath}")
        try:
            if await load_more.count() == 0:
                if verbose:
                    print("  No more '더보기' button.")
                break
            await load_more.first.click(timeout=3000)
        except Exception:
            if verbose:
                print("  '더보기' not clickable or gone.")
            break

        await page.wait_for_timeout(LOAD_MORE_WAIT_MS)
        new_count = await page.locator(f"xpath={anchors_xpath}").count()
        if new_count <= count:
            if verbose:
                print("  No new cards after '더보기'.")
            break
        # 다음 루프에서 새로 붙은 카드만 처리 (processed_count=end 부터 새 count 까지)
        count = new_count
    return results


async def run(
    urls: Iterable[str],
    *,
    out_csv: str,
    engine: str,
    headless: bool,
    limit_per_list: int,
    scroll_count: int,
    scroll_wait_ms: int,
    timeout_ms: int,
    anchors_xpath: str,
    info_wrapper_rel_xpath: str,
    list_title_rel_xpath: str,
    list_price_rel_xpath: str,
    list_neighborhood_rel_xpath: str,
    list_time_rel_xpath: str,
    list_description_rel_xpath: str,
    load_more_button_xpath: str,
) -> int:
    """
    크롤링 메인 로직.
    1) Playwright로 브라우저(Chromium 등) 실행
    2) daangn_urls.txt 에 있는 각 URL마다 scrape_list_page_items() 호출 → 리스트 페이지에서 카드 수집
    3) 중복(item_url 기준) 제거 후 CSV 한 번에 저장
    """
    from playwright.async_api import async_playwright

    _ensure_parent_dir(out_csv)
    scraped_at = _utc_now_iso()

    rows: list[ListingRow] = []
    seen_item_urls: set[str] = set()  # 같은 상품이 여러 URL 리스트에 나올 수 있으므로 중복 제거

    async with async_playwright() as p:
        browser_type = getattr(p, engine)  # chromium / webkit / firefox
        if not headless:
            print("Launching browser (visible window)...")
        browser = await browser_type.launch(headless=headless)
        context = await browser.new_context()
        try:
            list_page = await context.new_page()  # 탭 하나만 사용, URL만 바꿔가며 여러 리스트 수집
            try:
                for list_url in urls:
                    items = await scrape_list_page_items(
                        list_page,
                        list_url,
                        anchors_xpath=anchors_xpath,
                        info_wrapper_rel_xpath=info_wrapper_rel_xpath,
                        title_rel_xpath=list_title_rel_xpath,
                        price_rel_xpath=list_price_rel_xpath,
                        neighborhood_rel_xpath=list_neighborhood_rel_xpath,
                        time_rel_xpath=list_time_rel_xpath,
                        description_rel_xpath=list_description_rel_xpath,
                        load_more_button_xpath=load_more_button_xpath,
                        limit=limit_per_list,
                        scroll_count=scroll_count,
                        scroll_wait_ms=scroll_wait_ms,
                        timeout_ms=timeout_ms,
                        verbose=True,
                    )

                    # 수집한 카드들을 행으로 추가 (동일 상품 링크는 한 번만)
                    for item_url, title, description, price, neighborhood, time_text in items:
                        if item_url in seen_item_urls:
                            continue
                        seen_item_urls.add(item_url)

                        rows.append(
                            ListingRow(
                                source_list_url=list_url,
                                item_url=item_url,
                                title=title,
                                description=description,
                                price=price,
                                neighborhood=neighborhood,
                                time_text=time_text,
                                scraped_at_utc=scraped_at,
                            )
                        )
            finally:
                try:
                    await list_page.close()
                except Exception:
                    pass
        finally:
            try:
                await context.close()
            except Exception:
                pass  # e.g. browser already closed (Ctrl+C)
            try:
                await browser.close()
            except Exception:
                pass

    # --- 수집한 모든 행을 CSV 한 파일로 저장 ---
    # utf-8-sig: 엑셀에서 열 때 한글 깨짐 방지 (BOM 추가)
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "scraped_at_utc",
                "source_list_url",
                "item_url",
                "title",
                "description",
                "price",
                "neighborhood",
                "time_text",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "scraped_at_utc": r.scraped_at_utc,
                    "source_list_url": r.source_list_url,
                    "item_url": r.item_url,
                    "title": r.title,
                    "description": r.description,
                    "price": r.price,
                    "neighborhood": r.neighborhood,
                    "time_text": r.time_text,
                }
            )

    print(f"Saved CSV: {out_csv} ({len(rows)} rows)")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """터미널에서 넘긴 옵션 파싱 (--urls, --out, --headed, --limit, --scroll 등)."""
    ap = argparse.ArgumentParser(description="Scrape Daangn list/search page cards -> CSV (list only).")
    ap.add_argument("--urls", default="daangn_urls.txt", help="Text file with list/search URLs (one per line).")
    ap.add_argument("--out", default=os.path.join("out", "daangn", "listings.csv"), help="Output CSV path.")
    ap.add_argument("--engine", default="chromium", choices=["chromium", "webkit", "firefox"])
    ap.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default: true).")
    ap.add_argument(
        "--headed",
        action="store_true",
        help="Run browser with UI (sets headless=false).",
    )
    ap.add_argument("--limit", type=int, default=30, help="Max items per list page (0 = no limit).")
    ap.add_argument("--scroll", type=int, default=3, help="Auto-scroll count on list page to load more.")
    ap.add_argument("--scroll-wait-ms", type=int, default=900, help="Wait between scrolls (ms).")
    ap.add_argument("--timeout-ms", type=int, default=30000, help="Playwright timeout per operation.")

    # XPath/CSS override options (기본값은 현재 당근 웹 DOM 기준)
    ap.add_argument("--list-anchors-xpath", default=DEFAULT_LIST_CARD_ANCHORS_XPATH)
    ap.add_argument("--list-info-wrapper-rel-xpath", default=DEFAULT_LIST_INFO_WRAPPER_REL_XPATH)
    ap.add_argument("--list-title-rel-xpath", default=DEFAULT_LIST_TITLE_REL_XPATH)
    ap.add_argument("--list-price-rel-xpath", default=DEFAULT_LIST_PRICE_REL_XPATH)
    ap.add_argument("--list-neighborhood-rel-xpath", default=DEFAULT_LIST_NEIGHBORHOOD_REL_XPATH)
    ap.add_argument("--list-time-rel-xpath", default=DEFAULT_LIST_TIME_REL_XPATH)
    ap.add_argument("--list-description-rel-xpath", default=DEFAULT_LIST_DESCRIPTION_REL_XPATH, help="상품설명 요소 상대 XPath (없으면 빈 문자열)")
    ap.add_argument("--load-more-button-xpath", default=DEFAULT_LOAD_MORE_BUTTON_XPATH, help="'더보기' 버튼 XPath")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    """진입점: 인자 파싱 → URL 목록 읽기 → run() 비동기 실행."""
    args = _parse_args(argv)
    headless = False if args.headed else bool(args.headless)
    urls = _read_urls(args.urls)
    if not urls:
        raise SystemExit(f"No URLs found in {args.urls!r}")

    # asyncio.run() 이 run() 코루틴을 끝까지 실행 (브라우저 띄움 → 수집 → CSV 저장)
    return asyncio.run(
        run(
            urls,
            out_csv=args.out,
            engine=args.engine,
            headless=headless,
            limit_per_list=args.limit,
            scroll_count=args.scroll,
            scroll_wait_ms=args.scroll_wait_ms,
            timeout_ms=args.timeout_ms,
            anchors_xpath=args.list_anchors_xpath,
            info_wrapper_rel_xpath=args.list_info_wrapper_rel_xpath,
            list_title_rel_xpath=args.list_title_rel_xpath,
            list_price_rel_xpath=args.list_price_rel_xpath,
            list_neighborhood_rel_xpath=args.list_neighborhood_rel_xpath,
            list_time_rel_xpath=args.list_time_rel_xpath,
            list_description_rel_xpath=args.list_description_rel_xpath,
            load_more_button_xpath=args.load_more_button_xpath,
        )
    )


if __name__ == "__main__":
    # 예: python daangn_scrape.py --urls daangn_urls.txt --out out/daangn/listings.csv --limit 50 --headed
    raise SystemExit(main(os.sys.argv[1:]))
