from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse


def _ensure_out_dir(out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("_")
    return s or "output"


def _default_out_dir(url: str) -> str:
    p = urlparse(url)
    host = p.netloc or "out"
    return os.path.join("out", _safe_filename(host))


def _normalize_url(base_url: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("#"):
        return None
    if href.startswith("javascript:") or href.startswith("mailto:") or href.startswith("tel:"):
        return None
    return urljoin(base_url, href)


def _same_site(url: str, other: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(other).netloc
    except Exception:
        return False


@dataclass
class CrawlResult:
    url: str
    title: str | None
    text: str
    links: list[str]


def _extract_with_bs4(url: str, html: str, same_site_only: bool) -> CrawlResult:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else None

    # Remove noisy tags
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)

    links: list[str] = []
    for a in soup.select("a[href]"):
        normalized = _normalize_url(url, a.get("href"))
        if not normalized:
            continue
        if same_site_only and not _same_site(url, normalized):
            continue
        links.append(normalized)

    # De-duplicate while preserving order
    seen: set[str] = set()
    deduped = []
    for l in links:
        if l in seen:
            continue
        seen.add(l)
        deduped.append(l)

    return CrawlResult(url=url, title=title, text=text, links=deduped)


def fetch_html_requests(url: str, timeout_s: int = 30, user_agent: str | None = None) -> str:
    import requests

    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    resp = requests.get(url, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


async def fetch_html_browser(
    url: str,
    timeout_ms: int = 30000,
    user_agent: str | None = None,
    wait_until: str = "networkidle",
    screenshot_path: str | None = None,
    engine: str = "chromium",
    no_fallback: bool = False,
) -> tuple[str, str]:
    import asyncio
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # Some environments (especially sandboxed / arch-mismatch) can crash Chromium.
        # Default behavior: if engine=chromium and it fails, try webkit as a fallback.
        engines_to_try = [engine]
        if not no_fallback and engine == "chromium":
            engines_to_try.append("webkit")

        last_err: Exception | None = None
        op_timeout_s = max(1.0, timeout_ms / 1000.0)
        for eng in engines_to_try:
            browser = None
            context = None
            try:
                browser_type = getattr(p, eng)
                browser = await asyncio.wait_for(browser_type.launch(headless=True), timeout=op_timeout_s)
                context = await asyncio.wait_for(
                    (browser.new_context(user_agent=user_agent) if user_agent else browser.new_context()),
                    timeout=op_timeout_s,
                )
                page = await asyncio.wait_for(context.new_page(), timeout=op_timeout_s)

                await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                if screenshot_path:
                    await asyncio.wait_for(page.screenshot(path=screenshot_path, full_page=True), timeout=op_timeout_s)

                html = await asyncio.wait_for(page.content(), timeout=op_timeout_s)
                return html, eng
            except Exception as e:
                last_err = e
            finally:
                # Best-effort cleanup (avoid hanging forever on close)
                if context is not None:
                    try:
                        await asyncio.wait_for(context.close(), timeout=5.0)
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=5.0)
                    except Exception:
                        pass

        assert last_err is not None
        raise last_err


def _write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _write_json(path: str, obj: object) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Crawl a URL (requests or headless browser).")
    ap.add_argument("url", help="Target URL, e.g. https://example.com")
    ap.add_argument("--out", default=None, help="Output directory (default: out/<host>/)")
    ap.add_argument("--browser", action="store_true", help="Use Playwright (headless browser) to render JS.")
    ap.add_argument(
        "--engine",
        default="chromium",
        choices=["chromium", "webkit", "firefox"],
        help="Playwright browser engine (default: chromium).",
    )
    ap.add_argument(
        "--no-fallback",
        action="store_true",
        help="Disable fallback engine (chromium -> webkit) when chromium fails.",
    )
    ap.add_argument("--timeout", type=int, default=30, help="Timeout seconds (requests) / ~seconds (browser).")
    ap.add_argument("--user-agent", default=None, help="Custom User-Agent.")
    ap.add_argument("--same-site-only", action="store_true", help="Only keep links in same host.")
    ap.add_argument(
        "--wait-until",
        default="networkidle",
        choices=["load", "domcontentloaded", "networkidle", "commit"],
        help="Playwright waitUntil strategy.",
    )
    ap.add_argument("--screenshot", action="store_true", help="Save full-page screenshot when using --browser.")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    url = args.url
    out_dir = _ensure_out_dir(args.out or _default_out_dir(url))

    html_path = os.path.join(out_dir, "page.html")
    text_path = os.path.join(out_dir, "text.txt")
    links_path = os.path.join(out_dir, "links.json")
    meta_path = os.path.join(out_dir, "meta.json")
    screenshot_path = os.path.join(out_dir, "screenshot.png") if args.screenshot else None

    used_engine: str | None = None
    if args.browser:
        import asyncio

        html, used_engine = asyncio.run(
            fetch_html_browser(
                url,
                timeout_ms=int(args.timeout * 1000),
                user_agent=args.user_agent,
                wait_until=args.wait_until,
                screenshot_path=screenshot_path,
                engine=args.engine,
                no_fallback=args.no_fallback,
            )
        )
    else:
        html = fetch_html_requests(url, timeout_s=args.timeout, user_agent=args.user_agent)

    result = _extract_with_bs4(url, html, same_site_only=args.same_site_only)

    _write_text(html_path, html)
    _write_text(text_path, result.text + "\n")
    _write_json(links_path, result.links)
    _write_json(
        meta_path,
        {
            "url": result.url,
            "title": result.title,
            "links_count": len(result.links),
            "mode": "browser" if args.browser else "requests",
            "engine": used_engine,
            "out_dir": out_dir,
        },
    )

    print(f"Saved: {html_path}")
    if screenshot_path:
        print(f"Saved: {screenshot_path}")
    print(f"Saved: {text_path}")
    print(f"Saved: {links_path}")
    print(f"Saved: {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
