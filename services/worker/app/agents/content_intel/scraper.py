import os, random, asyncio
from typing import List, Dict
import yaml
from playwright.async_api import async_playwright

def load_sources(path: str = "/app/shared/trend_sources.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

async def _polite_sleep(rules: dict):
    lo, hi = rules.get("polite_delay_ms", [300, 900])
    await asyncio.sleep(random.randint(lo, hi) / 1000)

async def fetch_html(url: str, rules: dict) -> str:
    async with async_playwright() as p:
        context = await browser.new_context(user_agent=rules.get("user_agent"))
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _polite_sleep(rules)
        html = await page.content()
        await context.close()
        await browser.close()
        return html

async def fetch_many(urls: List[str], rules: dict, max_pages: int) -> List[Dict]:
    results = []
    for url in urls[:max_pages]:
        try:
            html = await fetch_html(url, rules)
            results.append({"url": url, "html": html})
        except Exception as e:
            results.append({"url": url, "error": str(e)})
    return results
