# SYSTEM_ARCHITECTURE.md

Hello To Natural Creator Discovery + Outreach System

---

## 1) What this system is

This project is a **creator discovery + intelligence + outreach operations platform** for Hello To Natural (H2N).

It is designed to:

* discover niche creators (micro + micro-UGC)
* enrich creators with lightweight public intel (followers, posts, bio signals)
* expand discovery through “related creator” graph expansion
* rank creators for outreach readiness
* generate drafts and track outreach lifecycle

The system intentionally uses **heuristics and best-effort scraping** (no official Instagram API).

---

## 2) High-level components

### A. Web App (FastAPI)

Provides:

* `/admin` dashboard
* `/admin/creators` list + filters + pagination
* `/admin/creators/{id}` creator profile page
* `/admin/graph` creator relationship visualization
* `/admin/intel` content intelligence outputs
* `/admin/outreach` campaign + drafts + events views

Also acts as an orchestration surface by exposing “Run jobs” buttons that trigger Celery tasks.

---

### B. Task Workers (Celery)

Long-running and IO-heavy work is done in Celery tasks:

* creator discovery (hashtags)
* related expansion discovery
* scoring + fraud checks
* graph build/update
* daily intelligence pipelines
* outreach batch generation

Workers run independently from the web service so the UI stays responsive.

---

### C. Scraper Layer (Playwright)

The project uses Playwright to fetch page HTML and extract:

* post shortcodes from hashtag/profile pages
* usernames from post pages
* follower counts + bios from profile pages

Important notes:

* a shared browser/context is used to reduce overhead and mimic normal browsing
* heavy resources are blocked (images/media/fonts) for speed
* pages may return “login walls” or “please wait a few minutes” throttles

---

### D. Discovery Engine (Heuristics)

Discovery is intentionally multi-stage:

1. **Hashtag discovery**

   * hashtag page → post shortcodes
   * post page → owner handle + caption mentions

2. **Enrichment + filtering**

   * profile page → followers/posts/bio
   * filter spam/brands/mega accounts
   * keep creators within a target follower band

3. **Related creator expansion**

   * from seed creators → extract recent post shortcodes
   * crawl their posts → mentions + coauthors
   * enrich + filter again
   * upsert new creators

Result: the system becomes less reliant on hashtags over time and more reliant on the creator community graph.

---

### E. Database (Postgres)

Stores:

* creators + discovery metadata
* graph edges (creator relationships)
* creator posts (optional)
* outreach campaigns
* outreach drafts and events
* intelligence outputs and metrics (optional)

Alembic manages schema migrations.

---

### F. Optional Local LLM (Ollama)

Used for:

* content intelligence ideation
* outreach messaging drafts
* classification heuristics (optional)

Because local inference can be slow, you may see timeouts unless models are sized correctly and timeouts are tuned.

---

## 3) System data flow

```text
Admin UI button click
   ↓
FastAPI route enqueues Celery task
   ↓
Celery worker runs job
   ↓
Playwright fetches HTML pages (cached where possible)
   ↓
Discovery engine extracts handles/relationships
   ↓
Enrichment filters by follower band + spam/brand heuristics
   ↓
Upsert creators + edges into Postgres
   ↓
Admin UI reads Postgres and renders tables/graphs/profiles
```

---

## 4) Key pipelines

### Pipeline 1: Hashtag Discovery

Purpose: acquire new candidates from topical niches.

Inputs:

* `seed_hashtags`
* per tag crawl limits
* follower band thresholds

Outputs:

* new creators (eligible)
* optionally excluded creators (audit trail)
* discovery notes (tags used)

---

### Pipeline 2: Related Creator Expansion

Purpose: expand from already-good seeds into their community.

Inputs:

* top N “seed creators” from DB
* profile-post crawl limits
* extraction from mentions + coauthors

Outputs:

* additional creators
* more graph edges
* discovery momentum without relying on hashtags

---

### Pipeline 3: Graph Build

Purpose: compute and persist creator relationships for “View graph”.

Inputs:

* current creator set
* similarity and relationship rules

Outputs:

* `creator_edges` with `edge_type`, `weight`, metadata

Graph is used for:

* neighborhood exploration
* repeated discovery expansions
* clustering / niche segmentation (future)

---

### Pipeline 4: Intelligence Engine

Purpose: generate content + signals for H2N.

Inputs:

* recent creator data
* trends/hashtags scraped (optional)
* local LLM (optional)

Outputs:

* `/admin/intel` viewable results
* daily “ideas” or patterns

---

### Pipeline 5: Outreach Batch

Purpose: produce ready-to-send outreach drafts.

Inputs:

* creators meeting eligibility
* campaign parameters
* brand voice rules
* guardrails

Outputs:

* `outreach_drafts` rows
* optional `outreach_events` history

---

## 5) Operating the system

### Recommended run cadence

**Bootstrapping phase (first 2–3 weeks):**

* Hashtag discovery: 2–4 times per day (small runs)
* Related expansion: once per day
* Graph build: once per day
* Intel engine: once per day
* Outreach batch: 2–3 times per week (after review)

**Steady state (after DB has a few thousand creators):**

* Hashtag discovery: 3–7 times per week
* Related expansion: 3–7 times per week (small-medium)
* Graph build: 2–3 times per week (or after major discovery pushes)
* Intel engine: daily
* Outreach batch: weekly (or per campaign)

Why this cadence works:

* the system becomes “graph-fed” instead of “hashtag-fed”
* reduces scraping load and risk
* keeps data fresh enough for outreach decisions

---

## 6) Micro-UGC prioritization strategy

Micro-UGC creators are typically:

* 1k–20k followers (sometimes 500–30k depending on niche)
* consistent posting
* strong content quality, not necessarily high follower count
* open to product-for-content collaborations

Recommended default band for micro-UGC:

* follower_min: 800–1500
* follower_max: 25k–35k
* hard_max_followers: 100k–150k

Additional signals to prioritize:

* bio includes “ugc”, “creator”, “content creator”
* email visible in bio
* high posting cadence
* posts have real comments (not spam)

---

## 7) Failure modes and what they mean

### A. “login wall” on hashtag pages

Meaning:

* Instagram is forcing login or throttling the IP/session.

Fix:

* use a logged-in storage state in Playwright context
* reduce frequency
* slow down requests
* use a dedicated scraper account

---

### B. `handles_found: 0` with `login_walls: N`

Meaning:

* scraper is blocked before it can extract `/p/` links.

Fix:

* storage state
* longer waits or wait-for-selector
* ensure JS content loads before `page.content()`
* use smaller runs with more delay

---

### C. Missing DB columns/tables errors

Meaning:

* DB schema not aligned with models.

Fix:

* ensure a single Alembic head
* upgrade head
* reset DB if test data only
* make migrations idempotent where possible

---

### D. Ollama timeouts

Meaning:

* model too slow for current timeout.

Fix:

* raise request timeout
* switch to smaller model for “fast” path
* limit tokens/temperature
* run Intel less frequently

---

## 8) Scaling strategy

### Scaling to 10k creators

* cache HTML aggressively
* reduce repeated enrichment of same creators
* introduce “last_scraped_at” thresholds so profiles aren’t scraped daily
* store discovery job metrics

### Scaling to 100k creators

* stop crawling profile pages repeatedly
* rely on graph expansion and incremental updates
* persist post shortcodes and avoid re-crawling
* move to queue-based enrichment (batch jobs)
* consider proxies and/or distributed crawlers if necessary (risk-aware)

---

## 9) Security and safety practices

* never use the primary H2N Instagram login for scraping
* store Playwright storage state securely
* do not commit session files to Git
* respect platform throttling signals
* avoid “write actions” (liking/following/commenting)

---

## 10) Recommended next upgrades (architecture-level)

Highest priority upgrades that improve robustness:

1. **Scrape rate governor**

   * global request budget per hour/day
   * auto backoff on throttle signals

2. **Job metrics dashboard**

   * login_walls, shortcodes extracted, posts crawled
   * enrichment success rate
   * created/updated/excluded counts over time

3. **Creator freshness policy**

   * don’t scrape the same profile more than every N days
   * prioritize “high score” creators for refresh

4. **Graph-driven discovery default**

   * only run hashtag discovery as “top-up”
   * expand from top clusters instead

5. **Micro-UGC scoring model**

   * weighted score combining:

     * niche match
     * follower band fit
     * posting cadence
     * engagement estimates
     * “ugc intent” signals

---

## 11) Glossary

* **Discovery:** finding new handles to add to DB
* **Enrichment:** collecting basic profile metadata (followers, bio, posts)
* **Filtering:** excluding spam/brands/mega accounts or out-of-band followers
* **Expansion:** finding related creators from a seed set
* **Graph:** relationship network between creators
* **UGC:** user generated content creator, often for product collabs
* **Micro-UGC:** small, niche creators optimized for authenticity and content quality