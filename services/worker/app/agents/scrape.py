from __future__ import annotations

import os
from playwright.async_api import async_playwright

import asyncio
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext

# Keep one browser per process, reuse across calls
_browser: Browser | None = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()

async def fetch_page_text(url: str, wait_ms: int = 1000) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(wait_ms)

        text = await page.inner_text("body")

        await browser.close()
        return text

async def _ensure_browser() -> tuple[Browser, BrowserContext]:
    global _browser, _context
    async with _lock:
        if _browser and _context:
            return _browser, _context

        p = await async_playwright().start()
        _browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        _context = await _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )
        return _browser, _context

async def fetch_page_html(
    url: str,
    *,
    wait_ms: int = 1200,
    timeout_ms: int = 60000,
) -> str:
    """
    Return raw page HTML using a shared browser/context to avoid relaunching Chromium
    on every request. Blocks images/media/fonts for speed.
    """
    _, context = await _ensure_browser()
    page = await context.new_page()

    # Block heavy resources
    await page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in {"image", "media", "font"}
        else route.continue_(),
    )

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(wait_ms)
        
        try:
            # Many IG pages include "/p/<shortcode>/" links; wait briefly for at least one
            await page.wait_for_function("() => document.body && document.body.innerHTML.includes('/p/')", timeout=3000)
        except Exception:
            pass

        html = await page.content()
        lower = html.lower()
        if "login" in lower and "instagram" in lower and "password" in lower:
            # login wall (best-effort)
            return html
        if "please wait a few minutes" in lower:
            return html
        return html
    finally:
        await page.close()

    