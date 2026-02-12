import argparse
import asyncio
import csv
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from playwright.async_api import async_playwright

RESULTS_DIR = Path("results")

# =========================
# 🔥 전역 설정 (인자 없을 때 기본값)
# =========================

HEADLESS = True          # True면 브라우저 안보임
SLOW_MO = 0               # 동작 느리게 보고 싶으면 100~300
TARGET_COUNT = 1000

ITEM_SELECTOR = "a[data-gtm='search_article']"
MORE_BUTTON_SELECTOR = "div[data-gtm='search_show_more_articles'] button"

# 상세 페이지 수집
DETAIL_PAGE_DELAY_MS = 800   # 배치/요청 간 대기 (ms)
DETAIL_PAGE_DELAY_MS_ON_FAIL = 200   # 상세 실패 시 다음 대기 (ms)
DETAIL_PAGE_TIMEOUT_MS = 15000
DETAIL_PAGE_CONCURRENCY = 4   # 동시 상세 수집 수 (2~5 권장)
DETAIL_PAGE_WAIT_SELECTOR = "#main-content article"   # 상세 로드 완료 판단용
DETAIL_PAGE_WAIT_TIMEOUT_MS = 5000
DETAIL_PAGE_FALLBACK_MS = 200   # selector 대기 실패 시 추가 대기

# 더보기 클릭 후 대기 (조건부)
MORE_BUTTON_POLL_INTERVAL_MS = 200   # 카드 수 증가 확인 간격
MORE_BUTTON_POLL_MAX_MS = 5000   # 최대 대기

# 리스트 첫 로드
LIST_PAGE_WAIT_SELECTOR_TIMEOUT_MS = 10000

# 당근 카테고리 (필터용)
ALL_CATEGORIES = [
    "디지털기기", "생활가전", "가구/인테리어", "생활/주방", "유아동", "유아도서",
    "여성의류", "여성잡화", "남성패션/잡화", "뷰티/미용", "스포츠/레저", "취미/게임/음반",
    "도서", "티켓/교환권", "e쿠폰", "가공식품", "건강기능식품", "반려동물용품", "식물",
    "기타 중고물품", "삽니다",
]
# 수집할 카테고리 (비어 있으면 필터 없음 = 전체 수집)
ALLOWED_CATEGORIES = ["디지털기기", "남성패션/잡화", "티켓/교환권", "e쿠폰"]

# =========================

def _build_search_url(
    keyword: str | None = None,
    region: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
) -> str:
    """검색 키워드·지역·가격으로 당근 검색 URL 생성. 가격은 price=최소__최대 형식."""
    base = "https://www.daangn.com/kr/buy-sell/"
    if keyword and keyword.strip():
        url = f"{base}?search={quote(keyword.strip())}"
    else:
        url = base
    # region 사용 시: url += f"&in={quote(region)}" if "?" in url else f"?in={quote(region)}"
    if min_price is not None or max_price is not None:
        price_val = f"{min_price or ''}__{max_price or ''}"
        url += "&" if "?" in url else "?"
        url += f"price={price_val}"
    return url


def _extract_detail_js() -> str:
    """상세 페이지에서 추가 정보를 추출하는 JS. 당근마켓 DOM에 맞게 셀렉터 수정 가능."""
    return """
    () => {
        const out = { title: "", description: "", image_count: "", seller_nickname: "", location: "", category: "", chat_count: "", interest_count: "", view_count: "", manner_temperature: "" };
        // 상세 페이지 제목 (h1)
        const titleEl = document.querySelector('#main-content article div._4y5lbr4 h1') || document.querySelector('#main-content article h1') || document.querySelector('article h1');
        if (titleEl) out.title = titleEl.innerText.trim();
        // 글 본문 (p._4y5lbrd)
        const descEl = document.querySelector('p._4y5lbrd') || document.querySelector('#main-content article p._4y5lbrd');
        if (descEl) {
            out.description = descEl.innerText.trim().replace(/\\n+/g, " ");
        }
        if (!out.description) {
            const bodySelectors = ["article section p", "article [class*='content']", "main section p", "article p"];
            for (const sel of bodySelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    out.description = el.innerText.trim().replace(/\\n+/g, " ");
                    if (out.description.length > 10) break;
                }
            }
        }
        const article = document.querySelector("article");
        if (article && !out.description) {
            const text = article.innerText.trim().replace(/\\n+/g, " ");
            if (text.length > 20) out.description = text;
        }
        // 카테고리 (section[2] div h2 > a[href*="category_id"]: "여성잡화")
        const catH2 = document.querySelector('#main-content article section:nth-of-type(2) div h2._4y5lbr9') || document.querySelector('#main-content article section:nth-of-type(2) div h2');
        if (catH2) {
            const catLink = catH2.querySelector('a[href*="category_id"]') || catH2.querySelector('a');
            if (catLink) out.category = catLink.innerText.trim();
        }
        // 채팅/관심/조회 (section[2] span._1pwsqmme: "채팅 22", "관심 33", "조회 1582")
        const section2 = document.querySelector('#main-content article section:nth-of-type(2)');
        if (section2) {
            const statSpans = section2.querySelectorAll('span._1pwsqmme');
            statSpans.forEach(s => {
                const t = s.innerText.trim();
                if (t.startsWith('채팅')) out.chat_count = t.replace(/[^0-9]/g, '');
                else if (t.startsWith('관심')) out.interest_count = t.replace(/[^0-9]/g, '');
                else if (t.startsWith('조회')) out.view_count = t.replace(/[^0-9]/g, '');
            });
        }
        // 매너 온도 (span.yzp7msi: "39.7°C")
        const tempSpan = document.querySelector('span.yzp7msi') || document.querySelector('#main-content article [class*="yzp7ms"] span');
        if (tempSpan && tempSpan.innerText.includes('°')) out.manner_temperature = tempSpan.innerText.trim();
        // 판매자 영역: 프로필+닉네임+동네
        const profileAnchor = document.querySelector('a[aria-label*="프로필"]');
        if (profileAnchor) {
            const container = profileAnchor.closest('div');
            if (container) {
                const userLinks = container.querySelectorAll('a[href*="/kr/users/"]');
                for (const a of userLinks) {
                    const t = a.innerText.trim();
                    if (t.length > 0 && t.length < 50 && !t.includes('프로필')) {
                        out.seller_nickname = t;
                        break;
                    }
                }
                const locLink = container.querySelector('a[href*="in="]');
                if (locLink) out.location = locLink.innerText.trim();
            }
        }
        // 이미지 개수
        const imgs = document.querySelectorAll("article img, [class*='image'] img, main img");
        if (imgs.length) out.image_count = String(imgs.length);
        return out;
    }
    """


async def _fetch_detail(page, url: str) -> dict:
    """상세 페이지에 들어가서 본문 등 추가 정보를 추출해 반환."""
    fail_result = {"title": "", "description": "", "image_count": "", "seller_nickname": "", "location": "", "category": "", "chat_count": "", "interest_count": "", "view_count": "", "manner_temperature": ""}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=DETAIL_PAGE_TIMEOUT_MS)
        try:
            await page.wait_for_selector(DETAIL_PAGE_WAIT_SELECTOR, timeout=DETAIL_PAGE_WAIT_TIMEOUT_MS)
        except Exception:
            await page.wait_for_timeout(DETAIL_PAGE_FALLBACK_MS)
        data = await page.evaluate(_extract_detail_js())
        return data
    except Exception as e:
        fail_result["description"] = f"[조회실패: {e!s}]"
        return fail_result


def _parse_args():
    parser = argparse.ArgumentParser(
        description="당근마켓 검색 크롤링",
        epilog="예시:  python carrot-rough-crawl.py --keyword 아이폰 --categories 디지털기기,티켓/교환권",
    )
    parser.add_argument("--keyword", "-k", default=None, help="검색 키워드 (생략 시 전체 리스트)")
    parser.add_argument(
        "--categories", "-c",
        default=None,
        help="수집할 카테고리 (쉼표 구분). 예: 디지털기기,남성패션/잡화,티켓/교환권,e쿠폰. 비우면 스크립트 기본값 사용, --no-filter 이면 전체 수집",
    )
    parser.add_argument("--no-filter", action="store_true", help="카테고리 필터 없이 전체 수집")
    parser.add_argument("--min-price", type=int, default=None, metavar="N", help="가격 최소값 (원). 예: 50000")
    parser.add_argument("--max-price", type=int, default=None, metavar="N", help="가격 최대값 (원). 예: 10000000")
    # parser.add_argument("--region", "-r", help="동네 (동이름-코드, 예: 역삼동-6035). 미사용 시 내 위치 기준")
    return parser.parse_args()


async def main(
    keyword: str | None = None,
    allowed_categories: list[str] | None = None,
    no_filter: bool = False,
    min_price: int | None = None,
    max_price: int | None = None,
):
    if no_filter:
        allowed_set = None
    elif allowed_categories is None:
        allowed_set = set(ALLOWED_CATEGORIES)
    else:
        allowed_set = set(allowed_categories) if allowed_categories else None

    search_url = _build_search_url(keyword, min_price=min_price, max_price=max_price)
    print("검색 URL:", search_url)
    print("키워드:", keyword if (keyword and keyword.strip()) else "(없음)")
    if min_price is not None or max_price is not None:
        print("가격 조건:", f"{min_price or '?'}원 ~ {max_price or '?'}원")
    if allowed_set:
        print("카테고리 필터:", ", ".join(sorted(allowed_set)))
    else:
        print("카테고리 필터: 없음 (전체 수집)")

    start_time = time.perf_counter()
    print("크롤링 시작")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            slow_mo=SLOW_MO
        )
        page = await browser.new_page()

        await page.goto(search_url, wait_until="domcontentloaded")
        await page.wait_for_selector(ITEM_SELECTOR, timeout=LIST_PAGE_WAIT_SELECTOR_TIMEOUT_MS)

        print("페이지 타이틀:", await page.title())

        prev_count = 0

        # =========================
        # 🔥 더보기 반복
        # =========================
        while True:
            cards = page.locator(ITEM_SELECTOR)
            count = await cards.count()
            print("현재 개수:", count)

            if count >= TARGET_COUNT:
                print("목표 개수 도달")
                break

            if count == prev_count:
                print("더 이상 증가하지 않음")
                break

            prev_count = count

            more_btn = page.locator(MORE_BUTTON_SELECTOR)

            if await more_btn.count() == 0:
                print("더보기 버튼 없음 → 종료")
                break

            if not await more_btn.is_enabled():
                print("더보기 버튼 비활성화 → 종료")
                break

            try:
                await more_btn.click()
                deadline = time.monotonic() + MORE_BUTTON_POLL_MAX_MS / 1000
                while True:
                    await asyncio.sleep(MORE_BUTTON_POLL_INTERVAL_MS / 1000)
                    new_count = await cards.count()
                    if new_count > prev_count:
                        break
                    if time.monotonic() >= deadline:
                        break
            except Exception as e:
                print("더보기 클릭 실패:", e)
                break

        # =========================
        # 데이터 추출
        # =========================
        
        items = await page.evaluate("""
        () => {
            const cards = document.querySelectorAll("a[data-gtm='search_article']");
            const results = [];

            cards.forEach(card => {

                const href = card.getAttribute("href") || "";
                const fullUrl = href ? "https://www.daangn.com" + href : "";

                // -------------------------
                // 1️⃣ wrapper
                // -------------------------
                const wrapper = card.querySelector(":scope > div");
                if (!wrapper) return;

                // wrapper 안에
                // [0] 썸네일 영역
                // [1] 텍스트 영역
                const children = wrapper.querySelectorAll(":scope > div");
                if (children.length < 2) return;

                const thumbnailArea = children[0];
                const textContainer = children[1];

                // -------------------------
                // 2️⃣ 판매상태 (썸네일 영역 안)
                // -------------------------
                let status = "판매중";
                const statusSpan = thumbnailArea.querySelector("span");
                if (statusSpan) {
                    const text = statusSpan.innerText.trim();
                    if (text === "예약중" || text === "거래완료") {
                        status = text;
                    }
                }

                // -------------------------
                // 3️⃣ info / meta 분리
                // -------------------------
                const textDivs = textContainer.querySelectorAll(":scope > div");
                if (textDivs.length < 2) return;

                const infoDiv = textDivs[0];
                const metaDiv = textDivs[1];

                const spans = infoDiv.querySelectorAll("span");

                const title = spans[0]?.innerText?.trim() || "";
                const price = spans[1]?.innerText?.trim() || "";

                const location = metaDiv.querySelector("span span")?.innerText?.trim() || "";
                const time = metaDiv.querySelector("time")?.innerText?.trim() || "";
                const categoryEl = card.querySelector('a[href*="category_id"]');
                const category = categoryEl ? categoryEl.innerText.trim() : "";

                if (!title) return;

                results.push({
                    title,
                    price,
                    location,
                    time,
                    status,
                    url: fullUrl,
                    category,
                    description: "",
                    image_count: ""
                });
            });

            return results;
        }
        """)

        # URL 기준 중복 제거 (끌올 등으로 같은 글이 여러 번 나올 수 있음)
        seen_urls = set()
        items_deduped = []
        for i in items:
            u = i.get("url") or ""
            if u and u not in seen_urls:
                seen_urls.add(u)
                items_deduped.append(i)
        if len(items_deduped) < len(items):
            print(f"URL 중복 제거: {len(items)} → {len(items_deduped)}건")
        items = items_deduped

        print(f"리스트 수집 개수: {len(items)}")

        # 리스트에서 카테고리 알 수 있으면 미리 필터 → 상세 방문 횟수 감소
        if allowed_set:
            known_allowed = [i for i in items if i.get("category") in allowed_set]
            unknown = [i for i in items if not (i.get("category") or "").strip()]
            items_to_detail = known_allowed + unknown  # 허용된 것 + 카테고리 미확인(상세에서 확인)
            skipped = len(items) - len(items_to_detail)
            if skipped > 0:
                print(f"카테고리 필터로 상세 생략: {skipped}건 (상세 수집 대상: {len(items_to_detail)}건)")
        else:
            items_to_detail = items

        # =========================
        # 🔥 상세 페이지 추가 수집 (병렬)
        # =========================
        total = len(items_to_detail)
        concurrency = min(DETAIL_PAGE_CONCURRENCY, total) if total else 0
        detail_pages = []

        if total > 0 and concurrency > 0:
            detail_pages = [await browser.new_page() for _ in range(concurrency)]
            print(f"상세 수집 병렬 수: {concurrency}")

        for chunk_start in range(0, total, concurrency if concurrency else 1):
            chunk = items_to_detail[chunk_start : chunk_start + concurrency]
            for i, item in enumerate(chunk):
                print(f"상세 페이지 수집 중 {chunk_start + i + 1}/{total} - {item.get('title', '')[:30]}...")
            tasks = [_fetch_detail(detail_pages[j], chunk[j]["url"]) for j in range(len(chunk))]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for j, item in enumerate(chunk):
                r = results[j]
                if isinstance(r, Exception):
                    extra = {"title": "", "description": f"[조회실패: {r!s}]", "image_count": "", "seller_nickname": "", "location": "", "category": "", "chat_count": "", "interest_count": "", "view_count": "", "manner_temperature": ""}
                else:
                    extra = r
                if extra.get("title"):
                    item["title"] = extra["title"]
                item["description"] = extra.get("description", "")
                item["image_count"] = extra.get("image_count", "")
                item["seller_nickname"] = extra.get("seller_nickname", "")
                if extra.get("location"):
                    item["location"] = extra["location"]
                item["category"] = extra.get("category", "")
                item["chat_count"] = extra.get("chat_count", "")
                item["interest_count"] = extra.get("interest_count", "")
                item["view_count"] = extra.get("view_count", "")
                item["manner_temperature"] = extra.get("manner_temperature", "")
            any_fail = any(
                isinstance(r, Exception) or ((r.get("description") or "").startswith("[조회실패") if isinstance(r, dict) else False)
                for r in results
            )
            delay_ms = DETAIL_PAGE_DELAY_MS_ON_FAIL if any_fail else DETAIL_PAGE_DELAY_MS
            if chunk_start + len(chunk) < total:
                await asyncio.sleep(delay_ms / 1000)

        for p in detail_pages:
            await p.close()

        # 상세에서 확인한 카테고리로 한 번 더 필터 (카테고리 미확인だった건 포함)
        if allowed_set:
            items_to_write = [i for i in items_to_detail if i.get("category") in allowed_set]
            print(f"카테고리 필터 결과: {len(items_to_write)}건 저장")
        else:
            items_to_write = items_to_detail

        # =========================
        # 🔥 CSV 저장 (results/년-월-일-시-분-초.csv)
        # =========================
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        out_path = RESULTS_DIR / f"{timestamp}.csv"
        fieldnames = ["title", "price", "location", "time", "status", "category", "seller_nickname", "image_count", "chat_count", "interest_count", "view_count", "manner_temperature", "description", "url"]
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(items_to_write)

        print(f"{out_path} 저장 완료")

        elapsed = time.perf_counter() - start_time
        m, s = divmod(int(elapsed), 60)
        if m > 0:
            print(f"총 크롤링 시간: {m}분 {s}초 ({elapsed:.1f}초)")
        else:
            print(f"총 크롤링 시간: {elapsed:.1f}초")

        await browser.close()


if __name__ == "__main__":
    args = _parse_args()
    if args.categories is not None:
        allowed = [c.strip() for c in args.categories.split(",") if c.strip()]
    else:
        allowed = None
    asyncio.run(main(
        keyword=args.keyword,
        allowed_categories=allowed,
        no_filter=args.no_filter,
        min_price=args.min_price,
        max_price=args.max_price,
    ))
