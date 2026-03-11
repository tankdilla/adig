# Creator Intelligence & Outreach Platform

*A creator discovery and outreach engine built for Hello To Natural*

This project is a **creator discovery, intelligence, and outreach automation system** designed to help identify and collaborate with niche creators across Instagram.

The system continuously discovers creators through hashtags, graph expansion, and audience signals, then ranks them based on niche relevance, growth, and similarity to successful partners.

The goal is to build a **high-quality database of micro-UGC creators** who are a strong fit for the Hello To Natural brand.

---

# Core Features

## Creator Discovery Engine

Discovers creators through multiple strategies:

* Instagram hashtag discovery
* Related creator expansion
* Graph-based creator relationships
* Audience seeding (planned)
* Comment mining (planned)

Discovery prioritizes **micro-creators** with follower counts between:

```
3k – 30k followers
```

These creators typically have:

* higher engagement
* lower collaboration costs
* more authentic content
* stronger conversion rates

---

## Creator Intelligence Engine

The system automatically analyzes creators and generates intelligence signals.

### Signals currently calculated

* niche relevance score
* follower growth (7d / 30d)
* partner similarity
* fraud/spam indicators
* brand vs creator classification

The intelligence system continuously updates as new data is discovered.

---

## Graph Expansion

Creators discovered via hashtags are used as **seed nodes**.

The system then expands outward by:

* analyzing related creators
* scanning posts for tagged users
* identifying network connections

This builds a **creator graph** similar to how influencer platforms work.

---

## Creator Admin Dashboard

Admin tools allow manual review of creators.

Routes:

```
/admin/creators
/admin/creators/{creator_id}
/admin/intel
```

These dashboards allow you to:

* review creators
* see growth signals
* inspect niche relevance
* view related creators
* inspect outreach history

---

## Creator Data Stored

Each creator includes fields like:

* handle
* platform
* follower estimate
* post count
* niche tags
* fraud flags
* niche score
* growth metrics
* relationship graph edges
* outreach history

---

# Architecture Overview

```
Discovery Tasks
      │
      ▼
Creator Database
      │
      ▼
Intel Engine
      │
      ▼
Creator Ranking
      │
      ▼
Outreach Campaigns
```

Key modules:

```
agents/
  outreach/
    discovery_engine.py
    intel_engine.py
    related_discovery.py

tasks/
  celery tasks for discovery & scoring

routes/
  admin dashboards

db_models.py
```

---

# Technology Stack

Core technologies used in the system:

### Backend

* Python
* FastAPI
* SQLAlchemy
* Alembic
* Celery
* Redis
* PostgreSQL

### Scraping

* Playwright

### Scheduling

* Celery workers
* Celery beat

### Infrastructure

* Docker
* Docker Compose

---

# Setup

## Start services

```
docker compose up --build
```

---

## Run database migrations

```
docker compose exec api alembic upgrade head
```

---

## Run discovery manually

```
tasks.creator_discovery_hashtags
```

---

## Run intel scoring

```
tasks.creator_intel_daily
```

---

# Task Scheduling

Recommended schedule.

### Discovery

```
every 6 hours
```

### Graph Expansion

```
every 12 hours
```

### Intelligence Engine

```
daily
```

---

# Creator Target Profile

The system is optimized to find **micro-UGC creators** who fit the Hello To Natural brand.

Target signals:

```
followers: 3k – 30k
```

Content themes:

* natural skincare
* body care
* holistic wellness
* plant based living
* faith + wellness
* PCOS / metabolic health
* herbal tea
* self care

---

# Security

The scraper may optionally use an Instagram authenticated session.

If used, the storage state file:

```
shared/ig_storage_state.json
```

must **never be committed to Git**.

Add to `.gitignore`:

```
shared/ig_storage_state.json
```

---

# Roadmap

Below is the prioritized roadmap for future upgrades.

The focus is on **improving creator discovery quality and campaign success rates**.

---

# Phase 1 — Discovery Improvements (Highest Priority)

Goal: dramatically increase the quality of discovered creators.

### Audience Seeding

Seed creators from:

* Hello To Natural followers
* commenters on brand posts
* creators mentioning brand hashtags

This often produces **the highest quality creator pool**.

---

### Comment Mining

Extract creators from:

* comments on niche posts
* commenters interacting with related creators

Commenters are often:

* smaller creators
* highly engaged
* open to collaborations

---

### Hashtag Auto-Expansion

Automatically learn new niche hashtags by analyzing posts that score highly.

Example:

```
#melaninskincare
#bodybutterlover
#selfcareclub
#pcosjourney
```

These hashtags then feed back into discovery.

---

# Phase 2 — Creator Intelligence

Goal: improve ranking and prioritization.

### Engagement Estimation

Estimate engagement using:

```
likes
comments
follower ratios
```

This helps identify creators with **authentic audiences**.

---

### Growth Detection

Highlight creators with rapid follower growth.

Signals:

```
7d growth
14d growth
30d growth
```

Fast-growing creators are often ideal partnership targets.

---

### UGC Probability Scoring

Predict whether a creator is likely to accept brand collaborations.

Signals include:

```
bio keywords
previous brand content
UGC mentions
```

---

# Phase 3 — Graph Intelligence

Goal: build a strong creator network graph.

### Creator Relationship Graph

Track:

```
mentions
tags
shared hashtags
collaborations
```

This allows discovery of entire creator communities.

---

### Community Clustering

Identify clusters such as:

* natural skincare creators
* wellness influencers
* PCOS educators
* herbal wellness communities

Clusters can become targeted outreach segments.

---

# Phase 4 — Outreach Automation

Goal: turn discovery into partnerships.

### Campaign Builder

Create outreach campaigns targeting:

```
creator niches
follower bands
growth rate
```

---

### AI-Generated Outreach Messages

Use creator context to generate personalized outreach.

Example:

```
Hi [creator],

We love your content about [topic].
We think you'd be a great fit for our [product].

Would you be interested in collaborating?
```

---

### Response Tracking

Track creator responses.

Statuses:

```
contacted
replied
interested
declined
partner
```

---

# Phase 5 — Creator Intelligence Platform

Goal: evolve into a full creator analytics platform.

### Similar Creator Discovery

Find creators similar to successful partners.

---

### Content Analysis

Analyze creator content themes automatically.

---

### Trend Detection

Identify trending topics within the niche.

---

### Campaign Performance Tracking

Measure:

```
traffic
sales
engagement
```

from creator collaborations.

---

# Long-Term Vision

The long-term goal is to build a **creator intelligence system** that:

* continuously discovers niche creators
* ranks them by brand fit
* identifies fast-growing influencers
* builds a graph of creator communities
* automates outreach and partnership management

This will allow Hello To Natural to build **a powerful creator ecosystem** that drives organic brand growth.

---

# License

Internal project for Hello To Natural.

---