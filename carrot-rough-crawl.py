import argparse
import asyncio
import csv
from urllib.parse import quote
from playwright.async_api import async_playwright

# =========================
# 🔥 전역 설정 (인자 없을 때 기본값)
# =========================

HEADLESS = False          # True면 브라우저 안보임
SLOW_MO = 0               # 동작 느리게 보고 싶으면 100~300
TARGET_COUNT = 1000

ITEM_SELECTOR = "a[data-gtm='search_article']"
MORE_BUTTON_SELECTOR = "div[data-gtm='search_show_more_articles'] button"

# =========================

def _build_search_url(keyword: str, region: str | None = None) -> str:
    """검색 키워드로 당근 검색 URL 생성."""
    base = "https://www.daangn.com/kr/buy-sell/"
    url = f"{base}?search={quote(keyword)}"
    # region 사용 시: url += f"&in={quote(region)}"
    return url


def _parse_args():
    parser = argparse.ArgumentParser(
        description="당근마켓 검색 크롤링",
        epilog="예시:  python carrot-rough-crawl.py --keyword 아이폰",
    )
    parser.add_argument("--keyword", "-k", required=True, help="검색 키워드")
    # parser.add_argument("--region", "-r", help="동네 (동이름-코드, 예: 역삼동-6035). 미사용 시 내 위치 기준")
    return parser.parse_args()


async def main(keyword: str):
    search_url = _build_search_url(keyword)
    print("검색 URL:", search_url)
    print("키워드:", keyword)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            slow_mo=SLOW_MO
        )
        page = await browser.new_page()

        await page.goto(search_url)
        await page.wait_for_load_state("networkidle")

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
                await page.wait_for_timeout(1500)
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

                if (!title) return;

                results.push({
                    title,
                    price,
                    location,
                    time,
                    status,
                    url: fullUrl
                });
            });

            return results;
        }
        """)


        print(f"총 수집 개수: {len(items)}")

        # =========================
        # 🔥 CSV 저장
        # =========================
        with open("result.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["title", "price", "location", "time", "status", "url"]
            )
            writer.writeheader()
            writer.writerows(items)

        print("result.csv 저장 완료")

        await browser.close()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(keyword=args.keyword))
