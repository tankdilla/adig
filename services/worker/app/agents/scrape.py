import os
from playwright.async_api import async_playwright

async def fetch_page_text(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)

        text = await page.inner_text("body")

        await browser.close()
        return text
