# Hello To Natural AI Growth Engine

AI-powered Instagram growth, creator discovery, and outreach automation for **Hello To Natural (H2N)**.

This system is designed to safely automate the parts of marketing that machines are good at while keeping **human approval for anything public-facing**.

The goal is to scale:

• Instagram growth
• influencer partnerships
• community engagement
• content insights

without using spammy automation or violating platform trust.

---

# System Overview

The platform runs a set of AI agents and supporting services.

Core idea:

Agents **think automatically**, but **actions require approval**.

Pipeline:

Discovery → Scoring → Insights → Drafts → Human Approval → Execution

---

# High Level Architecture

Docker containers run the system.

Services include:

API – FastAPI control plane and admin dashboard
Worker – Celery workers running AI agents
Scheduler – Celery Beat scheduled jobs
Database – Postgres storing creators, drafts, metrics
Redis – message broker and rate-limit store
Ollama – optional local LLM inference

---

# Directory Layout

```
h2n-agents
│
├── docker-compose.yml
├── .env
│
├── services
│   ├── api
│   │   ├── Dockerfile
│   │   └── app
│   │       ├── main.py
│   │       ├── db.py
│   │       ├── settings.py
│   │       ├── templates
│   │       └── alembic
│   │
│   ├── worker
│   │   ├── Dockerfile
│   │   └── app
│   │       ├── celery_app.py
│   │       ├── tasks.py
│   │       └── agents
│   │
│   └── scheduler
│
├── shared
│   ├── db_models.py
│   └── targeting.yaml
│
└── scripts
```

---

# Core AI Agents

## Content Intelligence Agent

Determines what content should be posted.

Analyzes:

• trending reels
• competitor accounts
• past H2N performance
• seasonal wellness topics

Outputs:

• reel ideas
• caption drafts
• posting schedule

---

# Creator Discovery Agent

Discovers potential collaborators.

Sources:

• Instagram hashtag pages
• manual imports
• similar creator graph

Focus niches:

• natural skincare
• shea butter / body oils
• herbal wellness
• plant-based lifestyle
• Black women wellness
• Christian / faith-based living
• natural hair community

---

# Creator Scoring System

Creators receive a score from 0 to 100.

Factors include:

Audience size
Niche alignment
Engagement signals
Authenticity
Brand safety
Fraud signals

Sweet spot:

5k – 80k followers

Creators above 250k are skipped.

---

# Fraud Detection

The system attempts to filter low-quality creators.

Signals include:

Low engagement relative to follower count
Very few posts
Spam keywords in profile
Bot-like behavior

Fraud reduces score or excludes the creator entirely.

---

# Creator Graph

The platform builds a graph of relationships between creators.

Edges represent:

Similarity
Audience overlap
Mentions
Collaborations

This enables:

Finding creators similar to successful partners
Avoiding overlapping audiences
Discovering rising creators early

---

# Outreach Automation

The system generates collaboration drafts.

Each outreach message is personalized using:

Creator niche
Recent content
Campaign context

Example message:

Hello {creator},

I really love your content around natural wellness.
We run Hello To Natural, a plant-based body care brand focused on rituals and holistic living.

We would love to send you something and collaborate if it feels aligned.

No pressure at all.

Mary & Darrell
Hello To Natural

---

# Safety System

The platform is intentionally conservative.

Two main protections:

Kill Switch
Action Mode

Kill switch blocks all automated actions.

Action modes:

review – generate drafts only
manual – export actions for manual use
live – execute approved tasks

Defaults are safe.

---

# Environment Variables

Example `.env`

```
POSTGRES_DB=h2n
POSTGRES_USER=h2n
POSTGRES_PASSWORD=change_me

REDIS_URL=redis://redis:6379/0

ADMIN_TOKEN=your_secure_token

ACTION_MODE=review
KILL_SWITCH=true

MAX_ACTIONS_PER_HOUR=30
MAX_DMS_PER_DAY=20
MAX_COMMENTS_PER_DAY=40
```

---

# Running the System

Build containers:

```
docker compose up --build
```

Run migrations:

```
docker compose exec api alembic upgrade head
```

Open admin dashboard:

```
http://localhost:8000
```

---

# Admin Interface

The control plane provides several views.

Dashboard
Creators
Outreach
Engagement
Logs
Pattern Reports

All actions require admin authentication.

---

# Creator Discovery Workflow

Typical weekly flow:

Sunday
Discover creators

Monday
Score creators

Tuesday
Generate outreach drafts

Wednesday
Send approved outreach

Friday
Review results

---

# Exclusion Rules

Creators are skipped when:

Follower count above 250k
Bio contains spam keywords
Account appears to be a store or brand
Very low activity

This keeps the database high quality.

---

# Database Models

Key entities include:

Creators
CreatorEdges
CreatorRelationships
PostDrafts
EngagementQueue
OutreachDrafts
ViralPatternReports

These power analytics and automation.

---

# Example Automation Pipeline

Discovery

↓

Fraud detection

↓

Scoring

↓

Similarity expansion

↓

Outreach draft generation

↓

Human approval

↓

Execution

---

# Content Insights

The system also analyzes reels and captions.

Outputs:

Common hooks
Typical reel length
Best performing CTAs

This improves future content.

---

# Development Workflow

When making code changes:

1. Update models
2. Create migration
3. Update worker agents
4. Test locally
5. Commit

---

# Future Improvements

Potential next upgrades:

TikTok creator discovery
YouTube Shorts ingestion
Pinterest discovery
Audience demographic modeling
Creator performance tracking
Automated giveaway coordination

---

# Notes

This system is designed to help grow Hello To Natural **organically and authentically**.

Automation supports the brand voice but does not replace it.

Mary and Darrell remain the voice of the brand.

---

# Maintainers

Hello To Natural

Built for internal growth operations.