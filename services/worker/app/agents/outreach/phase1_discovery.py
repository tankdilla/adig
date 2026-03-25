from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from html import unescape
from typing import Iterable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

USER_AGENT = os.getenv("DISCOVERY_USER_AGENT", "Mozilla/5.0 (compatible; H2NDiscoveryBot/0.1; +https://hellotonatural.com)")
TIMEOUT_SECONDS = float(os.getenv("DISCOVERY_HTTP_TIMEOUT", "12"))

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
INSTAGRAM_HANDLE_RE = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9._]{2,30})", re.I)
YOUTUBE_HANDLE_RE = re.compile(r"(?:youtube\.com/|www\.youtube\.com/)?@([A-Za-z0-9._-]{2,40})", re.I)
SUBSCRIBERS_RE = re.compile(r"([0-9][0-9,\.]*)\s*(K|M)?\s+subscribers", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
SCRIPT_JSON_RE = re.compile(r"ytInitialData\s*=\s*(\{.*?\})\s*;", re.S)

NICHE_KEYWORDS = {
    "natural wellness": ["natural", "wellness", "herbal", "holistic", "wellness routine"],
    "body care": ["body care", "body butter", "soap", "skincare", "skin care", "self care"],
    "faith": ["christian", "faith", "devotional", "church", "bible"],
    "tea": ["tea", "herbal tea", "tisane"],
    "women lifestyle": ["mom life", "women", "lifestyle", "routine"],
}


@dataclass
class DiscoveryCandidate:
    handle: str
    platform: str = "instagram"
    source_platforms: set[str] = field(default_factory=set)
    source_urls: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    website_url: str | None = None
    followers_est: int | None = None
    confidence_score: float = 0.0
    niche_tags: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def merge(self, other: "DiscoveryCandidate") -> None:
        self.source_platforms |= other.source_platforms
        self.source_urls |= other.source_urls
        self.emails |= other.emails
        self.niche_tags |= other.niche_tags
        self.notes.extend([n for n in other.notes if n not in self.notes])
        self.confidence_score = max(self.confidence_score, other.confidence_score)
        if other.followers_est and (self.followers_est is None or other.followers_est > self.followers_est):
            self.followers_est = other.followers_est
        if not self.website_url and other.website_url:
            self.website_url = other.website_url


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


def _fetch_url(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def google_search(query: str, max_results: int = 5) -> list[SearchResult]:
    html = _fetch_url(f"https://www.google.com/search?q={quote_plus(query)}&num={max_results}")
    results: list[SearchResult] = []
    seen: set[str] = set()
    for href in HREF_RE.findall(html):
        if not href.startswith("/url?q="):
            continue
        raw = href.split("/url?q=", 1)[1].split("&", 1)[0]
        url = unescape(raw)
        if url in seen:
            continue
        if any(block in url for block in ["google.com", "youtube.com/results", "accounts.google.com"]):
            continue
        seen.add(url)
        results.append(SearchResult(title=url, url=url, snippet=""))
        if len(results) >= max_results:
            break
    return results


def youtube_search_html(query: str) -> str:
    return _fetch_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}")


def _clean_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_subscriber_count(text: str | None) -> int | None:
    if not text:
        return None
    m = SUBSCRIBERS_RE.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").upper()
    if suffix == "K":
        value *= 1_000
    elif suffix == "M":
        value *= 1_000_000
    return int(value)


def _tags_from_text(text: str) -> set[str]:
    text_l = (text or "").lower()
    tags = set()
    for tag, keywords in NICHE_KEYWORDS.items():
        if any(keyword in text_l for keyword in keywords):
            tags.add(tag)
    return tags


def parse_youtube_search_results(html: str, source_url: str = "https://www.youtube.com/results") -> list[DiscoveryCandidate]:
    candidates: dict[str, DiscoveryCandidate] = {}

    json_match = SCRIPT_JSON_RE.search(html)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
        except Exception:
            data = None
        if isinstance(data, dict):
            for handle in set(YOUTUBE_HANDLE_RE.findall(json.dumps(data))):
                normalized = normalize_handle(handle)
                if not normalized:
                    continue
                bucket = candidates.setdefault(normalized, DiscoveryCandidate(handle=normalized))
                bucket.source_platforms.add("youtube")
                bucket.source_urls.add(source_url)
                bucket.confidence_score = max(bucket.confidence_score, 0.65)

    text = _clean_text(html)
    tags = _tags_from_text(text)
    for handle in set(YOUTUBE_HANDLE_RE.findall(html)):
        normalized = normalize_handle(handle)
        if not normalized:
            continue
        bucket = candidates.setdefault(normalized, DiscoveryCandidate(handle=normalized))
        bucket.source_platforms.add("youtube")
        bucket.source_urls.add(source_url)
        bucket.confidence_score = max(bucket.confidence_score, 0.75)
        bucket.niche_tags |= tags
        subs = _parse_subscriber_count(text)
        if subs:
            bucket.followers_est = max(bucket.followers_est or 0, subs)
        bucket.notes.append("Discovered from YouTube search")

    return list(candidates.values())


def parse_curated_article(html: str, source_url: str) -> list[DiscoveryCandidate]:
    text = _clean_text(html)
    tags = _tags_from_text(text)
    emails = set(EMAIL_RE.findall(text))
    links = HREF_RE.findall(html)
    candidates: dict[str, DiscoveryCandidate] = {}
    for href in links:
        match = INSTAGRAM_HANDLE_RE.search(href)
        if not match:
            continue
        handle = normalize_handle(match.group(1))
        if not handle:
            continue
        bucket = candidates.setdefault(handle, DiscoveryCandidate(handle=handle))
        bucket.source_platforms.add("web")
        bucket.source_urls.add(source_url)
        bucket.niche_tags |= tags
        bucket.emails |= emails
        bucket.confidence_score = max(bucket.confidence_score, 0.7)
        if not bucket.website_url and href.startswith("http") and "instagram.com" not in href:
            bucket.website_url = href
        bucket.notes.append("Mentioned in curated web list")

    text_without_emails = EMAIL_RE.sub(" ", text)
    for handle in set(INSTAGRAM_HANDLE_RE.findall(text_without_emails)):
        handle = normalize_handle(handle)
        if not handle:
            continue
        bucket = candidates.setdefault(handle, DiscoveryCandidate(handle=handle))
        bucket.source_platforms.add("web")
        bucket.source_urls.add(source_url)
        bucket.niche_tags |= tags
        bucket.emails |= emails
        bucket.confidence_score = max(bucket.confidence_score, 0.55)
        bucket.notes.append("Instagram handle found in article text")

    title_match = TITLE_RE.search(html)
    title = _clean_text(title_match.group(1)) if title_match else ""
    if title:
        article_tags = _tags_from_text(title)
        for bucket in candidates.values():
            bucket.niche_tags |= article_tags
    return list(candidates.values())


def normalize_handle(handle: str | None) -> str | None:
    if not handle:
        return None
    handle = handle.strip().lstrip("@").strip().strip("/").lower()
    if not handle:
        return None
    if any(ch.isspace() for ch in handle):
        handle = handle.split()[0]
    if not re.fullmatch(r"[a-z0-9._]{2,30}", handle):
        return None
    return handle


def score_candidate(candidate: DiscoveryCandidate) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 15

    if candidate.emails:
        score += 20
        reasons.append("has email")
    if candidate.website_url:
        score += 10
        reasons.append("has website")
    if len(candidate.source_platforms) >= 2:
        score += 15
        reasons.append("multi source")
    elif candidate.source_platforms:
        score += 8
        reasons.append("single source")

    if candidate.followers_est is not None:
        if 5_000 <= candidate.followers_est <= 80_000:
            score += 20
            reasons.append("micro creator range")
        elif 2_000 <= candidate.followers_est < 5_000 or 80_000 < candidate.followers_est <= 150_000:
            score += 10
            reasons.append("adjacent audience range")
        elif candidate.followers_est > 500_000:
            score -= 10
            reasons.append("too large for priority")

    if candidate.niche_tags:
        niche_points = min(25, len(candidate.niche_tags) * 8)
        score += niche_points
        reasons.append("niche alignment")

    if candidate.confidence_score >= 0.75:
        score += 10
        reasons.append("high confidence")
    elif candidate.confidence_score >= 0.55:
        score += 5
        reasons.append("medium confidence")

    score = max(0, min(100, score))
    return score, reasons


def merge_candidates(candidates: Iterable[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    merged: dict[str, DiscoveryCandidate] = {}
    for candidate in candidates:
        handle = normalize_handle(candidate.handle)
        if not handle:
            continue
        candidate.handle = handle
        bucket = merged.get(handle)
        if bucket is None:
            merged[handle] = candidate
        else:
            bucket.merge(candidate)
    return list(merged.values())


def discover_phase1(*, queries: list[str], max_google_results: int = 5, per_query_youtube: bool = True, per_query_google: bool = True) -> list[DiscoveryCandidate]:
    all_candidates: list[DiscoveryCandidate] = []
    for query in queries:
        if per_query_youtube:
            yt_html = youtube_search_html(query)
            all_candidates.extend(parse_youtube_search_results(yt_html, source_url=f"youtube:{query}"))
        if per_query_google:
            results = google_search(query, max_results=max_google_results)
            for result in results:
                try:
                    html = _fetch_url(result.url)
                except Exception:
                    continue
                all_candidates.extend(parse_curated_article(html, source_url=result.url))
    return merge_candidates(all_candidates)
