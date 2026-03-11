from __future__ import annotations

import asyncio
import os
from typing import Optional, Tuple

from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

# ----------------------------
# Shared, long-lived objects
# ----------------------------
_playwright: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


async def _ensure_browser() -> Tuple[Browser, BrowserContext]:
    """
    Keep one Playwright driver, one Browser, one Context per process.
    Context can optionally load storage_state for logged-in Instagram access.
    """
    global _playwright, _browser, _context

    async with _lock:
        if _browser and _context:
            return _browser, _context

        if _playwright is None:
            _playwright = await async_playwright().start()

        # Optional proxy support
        proxy_server = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
        proxy = {"server": proxy_server} if proxy_server else None

        _browser = await _playwright.chromium.launch(
            headless=_env_bool("PLAYWRIGHT_HEADLESS", True),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            proxy=proxy,
        )

        # Optional authenticated IG session
        storage_state_path = os.getenv("IG_STORAGE_STATE", "/shared/ig_storage_state.json")
        use_storage_state = os.path.exists(storage_state_path)

        context_kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 720},
        )
        if use_storage_state:
            context_kwargs["storage_state"] = storage_state_path

        _context = await _browser.new_context(**context_kwargs)
        return _browser, _context


async def _maybe_accept_cookies(page) -> None:
    """
    Instagram sometimes blocks content behind a consent modal.
    This is best-effort: ignore failures.
    """
    try:
        for label in [
            "Allow all cookies",
            "Accept all",
            "Accept All",
            "Only allow essential cookies",
            "Allow essential cookies",
        ]:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click(timeout=1500)
                await page.wait_for_timeout(300)
                break
    except Exception:
        pass


def _looks_like_login_wall(html: str) -> bool:
    lower = (html or "").lower()
    # Strong signals
    if "accounts/login" in lower:
        return True
    if "please wait a few minutes" in lower:
        return True
    # Common login gate content
    if ("login" in lower and "password" in lower and "instagram" in lower):
        return True
    if ("log in to instagram" in lower) or ("sign up to see photos" in lower):
        return True
    return False


async def fetch_page_text(url: str, wait_ms: int = 1000) -> str:
    """
    Prefer fetch_page_html() for IG. This is still here for older code paths.
    """
    html = await fetch_page_html(url, wait_ms=wait_ms)
    # quick-and-dirty body text extraction
    return html


async def fetch_page_html(
    url: str,
    *,
    wait_ms: int = 1200,
    timeout_ms: int = 60000,
    scroll: bool = True,
) -> str:
    """
    Return raw page HTML using a shared browser/context.

    Key IG behaviors:
    - Often redirects to /accounts/login when blocked
    - Often requires a scroll to render /p/<shortcode>/ anchors
    - Often shows cookie consent modal
    """
    _, context = await _ensure_browser()
    page = await context.new_page()

    async def _route_handler(route):
        rtype = route.request.resource_type
        if rtype in {"image", "media", "font"}:
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", _route_handler)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # Hard redirect check (more reliable than HTML keyword checks)
        if "accounts/login" in page.url:
            html = await page.content()
            return html

        await _maybe_accept_cookies(page)

        # Many IG pages need a scroll to render anchors
        if scroll:
            try:
                await page.mouse.wheel(0, 2200)
                await page.wait_for_timeout(600)
                await page.mouse.wheel(0, 2200)
                await page.wait_for_timeout(600)
            except Exception:
                pass

        # Wait for /p/ anchors if they appear
        try:
            await page.wait_for_selector("a[href*='/p/']", timeout=12_000)
        except Exception:
            pass

        # Extra wait for client-side hydration
        await page.wait_for_timeout(wait_ms)

        html = await page.content()
        return html
    finally:
        try:
            await page.unroute("**/*", _route_handler)
        except Exception:
            pass
        await page.close()