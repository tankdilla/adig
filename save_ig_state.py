import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # opens a real browser window
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        print("Log in to Instagram in the opened window. Then come back here and press Enter...")
        input()

        # Save cookies + localStorage/sessionStorage
        await context.storage_state(path="shared/ig_storage_state.json")
        await browser.close()
        print("Saved shared/ig_storage_state.json")

asyncio.run(main())