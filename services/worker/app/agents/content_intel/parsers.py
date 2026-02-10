from bs4 import BeautifulSoup
from typing import List, Dict

def parse_google_trends_rss(xml_text: str) -> List[Dict]:
    # RSS is XML; BeautifulSoup handles it.
    soup = BeautifulSoup(xml_text, "xml")
    items = []
    for item in soup.find_all("item")[:20]:
        title = (item.title.text or "").strip()
        traffic = (item.find("ht:approx_traffic").text if item.find("ht:approx_traffic") else "").strip()
        items.append({"trend": title, "traffic": traffic})
    return items

def parse_youtube_results(html: str) -> List[Dict]:
    # YouTube is JS-heavy; this will be imperfect.
    # We use title tags as a rough signal (still helpful).
    soup = BeautifulSoup(html, "html.parser")
    titles = []
    for a in soup.select("a#video-title")[:20]:
        t = (a.get("title") or "").strip()
        if t:
            titles.append({"title": t})
    return titles

def parse_reddit_titles(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    # Modern Reddit varies; capture common patterns
    titles = []
    for h3 in soup.find_all("h3")[:25]:
        t = (h3.get_text() or "").strip()
        if 12 <= len(t) <= 140:
            titles.append({"title": t})
    return titles
