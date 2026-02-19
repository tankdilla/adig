# Hello To Natural — AI Content Engine

An AI-driven content intelligence and automation system designed to grow **Hello To Natural’s Instagram presence from 18K to 36K followers** using structured trend ingestion, local LLM ideation, and reviewable draft pipelines.

This system runs locally on Apple Silicon (M3, 16GB RAM) using Docker, Celery, PostgreSQL, Redis, Playwright, and Ollama.

---

## Overview

The Hello To Natural AI Content Engine:

* Scrapes non-Instagram trend sources (Google Trends RSS, YouTube, Reddit, etc.)
* Synthesizes trend signals
* Generates structured Reel ideas via a local LLM (Ollama)
* Stores reviewable drafts in Postgres
* Allows on-demand generation from an admin interface
* Runs fully containerized

It is built for:

* Sustainable, compliant growth
* Human-in-the-loop review
* Local-first privacy
* Modular expansion

---

## Architecture

```
                    ┌──────────────────────┐
                    │     FastAPI API      │
                    │   Admin Interface    │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │      Redis Queue     │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │     Celery Worker    │
                    │  Content Pipeline    │
                    └──────────┬───────────┘
                               │
                               ▼
        ┌───────────────┬───────────────┬────────────────┐
        │  Playwright   │   RSS Parser  │  LLM (Ollama)  │
        │  Scraping     │   Signals     │  Idea Generator│
        └───────────────┴───────────────┴────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │      PostgreSQL      │
                    │ post_drafts          │
                    │ daily_plans          │
                    └──────────────────────┘
```

---

## Core Features

### 1. Content Intel Pipeline

* Google Trends RSS ingestion
* Trend signal parsing
* Structured signal normalization
* Resilient error handling
* JSON-safe idea generation

### 2. LLM-Powered Ideation

* Local inference via Ollama
* Structured JSON output enforcement
* Self-repair JSON validation
* Brand-aligned system prompt
* Fast vs. thoughtful model separation

### 3. Admin UI

* Manual “Generate Today’s Ideas” button
* Review drafts before publishing
* View daily content plan
* Rate-limit generation to prevent abuse

### 4. Database Models

* `post_drafts`
* `daily_plans`
* `settings`

---

## Tech Stack

| Layer         | Technology           |
| ------------- | -------------------- |
| API           | FastAPI              |
| Worker        | Celery               |
| Broker        | Redis                |
| Database      | PostgreSQL           |
| LLM           | Ollama (llama3.1:8b) |
| Scraping      | Playwright           |
| Parsing       | XML + BeautifulSoup  |
| Orchestration | Docker Compose       |

---

## Running Locally

### 1. Start Services

```bash
docker compose up -d --build
```

### 2. Access Admin

```
http://localhost:8000/admin
```

### 3. Generate Ideas

Click **Generate Today’s Ideas**

### 4. Inspect Database

```bash
docker compose exec db psql -U h2n -d h2n
```

---

## Environment Variables

```
DATABASE_URL=postgresql+psycopg://h2n:h2n_password@db:5432/h2n
REDIS_URL=redis://redis:6379/0
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=llama3.1:8b
OLLAMA_MODEL_FAST=llama3.1:8b
TREND_SOURCES_PATH=/app/shared/trend_sources.yaml
```

---

# Roadmap

The system is intentionally modular. Below is the planned evolution.

---

## Phase 1 — Stability & Reliability (Next)

* [✅] Add structured logging
* [ ] Add pipeline health dashboard in admin
* [ ] Show last run time + idea count
* [ ] Model availability check at startup
* [ ] Per-source success/failure reporting
* [ ] Improve prompt reliability for strict JSON

---

## Phase 2 — Growth Acceleration Engine

* [ ] Auto-caption A/B variants
* [ ] Hook optimization scoring
* [ ] Engagement probability scoring model
* [ ] Reel format templates (educational, testimonial, product, storytime)
* [ ] Hashtag intelligence weighting
* [ ] Weekly content calendar auto-builder
* [ ] Topic clustering to avoid repetition

---

## Phase 3 — Creator Discovery Agent

* [ ] Niche influencer discovery (non-Instagram scraping)
* [ ] Engagement quality scoring
* [ ] Collaboration suggestion engine
* [ ] Outreach draft generator
* [ ] CRM table for creators
* [ ] Email/DM template personalization

---

## Phase 4 — Performance Feedback Loop

* [ ] Manual performance input (views, saves, shares)
* [ ] Auto-learning hook optimizer
* [ ] Signal weighting adjustments
* [ ] Top-performing pattern detection
* [ ] Idea pruning algorithm

---

## Phase 5 — Automated Publishing Layer (Optional, compliant)

* [ ] Generate export-ready caption packs
* [ ] Canva script generation
* [ ] CapCut script export
* [ ] Calendar ICS generation
* [ ] Safe reminder scheduler
* [ ] Human approval queue

---

## Phase 6 — Strategic Expansion

* [ ] YouTube Shorts adaptation
* [ ] Pinterest idea conversion
* [ ] Blog post auto-expansion
* [ ] Email newsletter auto-draft
* [ ] Product integration suggestions
* [ ] Affiliate content variant generator

---

## Future Infrastructure Improvements

* [ ] Replace Celery with Temporal (advanced workflows)
* [ ] Switch to structured event streaming
* [ ] Vector memory store for idea uniqueness
* [ ] Model router (fast vs. reasoning vs. rewrite)
* [ ] GPU-accelerated local inference
* [ ] Automated model benchmarking

---

# Design Philosophy

This system is built around:

* Human oversight
* Compliance safety
* Non-bot engagement growth
* Deterministic data storage
* Modular AI agents
* Local-first privacy

It is not designed to automate engagement spam or violate platform terms.

---

# Long-Term Vision

Transform Hello To Natural into:

* A high-output content brand
* A community-driven wellness authority
* A repeatable AI-powered marketing engine
* A blueprint for other local-first AI business stacks

---

# Contributing

Future improvements should:

* Preserve structured JSON contracts
* Avoid brittle scraping dependencies
* Maintain human-in-the-loop design
* Prefer deterministic over “magical” behavior
* Include logging for all AI calls

---

# License

Private internal use for Hello To Natural.

---