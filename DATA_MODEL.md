# DATA_MODEL.md

Creator Intelligence Data Model

---

# Overview

This document describes the database schema used by the **Creator Discovery and Intelligence System**.

The system stores creators, relationships, content signals, outreach events, and intelligence metrics that help prioritize collaborations.

The schema is designed to support:

* creator discovery
* creator ranking
* creator graph expansion
* outreach tracking
* campaign analytics

---

# Core Entity: Creator

The **Creator** table represents a social media creator discovered by the system.

Each record represents a unique creator account.

Example fields:

| Field         | Description                            |
| ------------- | -------------------------------------- |
| id            | unique database identifier             |
| handle        | social media handle                    |
| platform      | platform name (instagram, etc)         |
| followers_est | estimated follower count               |
| posts_count   | number of posts                        |
| niche_tags    | tags associated with creator discovery |
| score         | internal ranking score                 |
| fraud_score   | spam / authenticity risk score         |
| is_brand      | detected brand account                 |
| is_spam       | detected spam account                  |
| created_at    | when creator was first discovered      |

Additional intelligence fields:

| Field             | Description                                     |
| ----------------- | ----------------------------------------------- |
| niche_score       | how closely the creator matches brand niche     |
| growth_7d         | follower growth in last 7 days                  |
| growth_30d        | follower growth in last 30 days                 |
| last_intel_run_at | last time creator intelligence was updated      |
| is_partner        | indicates creator is a successful brand partner |

---

# Creator Metrics

Creator growth and engagement signals are stored separately.

Table: `creator_metrics_daily`

This table records snapshots of creator metrics over time.

| Field           | Description                |
| --------------- | -------------------------- |
| creator_id      | associated creator         |
| snapshot_date   | date of snapshot           |
| followers_est   | follower count estimate    |
| posts_count     | number of posts            |
| avg_like_est    | estimated average likes    |
| avg_comment_est | estimated average comments |

This enables calculations like:

* follower growth
* engagement trends
* creator momentum

---

# Creator Signals

The `creator_signals` table stores evidence supporting niche classification.

Signals are extracted from:

* bios
* post captions
* hashtags
* related content

Example fields:

| Field       | Description                           |
| ----------- | ------------------------------------- |
| creator_id  | associated creator                    |
| signal_type | source of signal (bio, post, hashtag) |
| signal_text | extracted content                     |
| weight      | importance of signal                  |
| source_url  | post/profile where signal was found   |

Signals contribute to the **niche_score** used for ranking creators.

---

# Creator Graph

Creators often form communities connected by:

* mentions
* tags
* collaborations
* shared hashtags

These relationships are stored in graph tables.

Example structure:

Table: `creator_edges`

| Field             | Description           |
| ----------------- | --------------------- |
| source_creator_id | originating creator   |
| target_creator_id | connected creator     |
| edge_type         | mention, tag, co-post |
| weight            | relationship strength |

Graph expansion uses these edges to discover additional creators.

---

# Creator Posts

Optional table for storing creator posts.

Table: `creator_posts`

Example fields:

| Field         | Description       |
| ------------- | ----------------- |
| creator_id    | creator           |
| shortcode     | Instagram post ID |
| posted_at     | timestamp         |
| caption       | post caption      |
| like_count    | likes             |
| comment_count | comments          |

Post data enables deeper analysis of:

* creator content
* engagement rates
* niche alignment

---

# Outreach Campaigns

Creators may be contacted through outreach campaigns.

Table: `outreach_campaigns`

| Field      | Description           |
| ---------- | --------------------- |
| id         | campaign identifier   |
| name       | campaign name         |
| created_at | when campaign started |

Example campaigns:

* product launch
* seasonal promotion
* ambassador program

---

# Outreach Drafts

Messages prepared for creators.

Table: `outreach_drafts`

| Field        | Description              |
| ------------ | ------------------------ |
| creator_id   | associated creator       |
| campaign_id  | campaign                 |
| message_text | outreach message         |
| created_at   | when draft was generated |

Drafts allow review before sending messages.

---

# Outreach Events

Records interactions with creators.

Table: `outreach_events`

| Field             | Description             |
| ----------------- | ----------------------- |
| outreach_draft_id | associated draft        |
| event_type        | sent, replied, declined |
| note              | additional context      |
| created_at        | timestamp               |

This enables tracking the outreach lifecycle.

---

# Example Creator Lifecycle

Example flow:

```
Hashtag Discovery
      ↓
Creator Created
      ↓
Graph Expansion
      ↓
Signals Extracted
      ↓
Intel Engine Runs
      ↓
Creator Ranked
      ↓
Outreach Campaign
      ↓
Partnership Established
```

---

# Scaling Considerations

As the database grows to tens of thousands of creators, consider:

* indexing frequently queried fields
* partitioning historical metrics
* caching creator ranking results
* limiting expensive graph queries

---

# Future Data Model Enhancements

Planned schema improvements include:

### Creator Engagement Metrics

Store deeper engagement signals:

* engagement rate
* average views
* comment sentiment

---

### Creator Audience Insights

Analyze follower demographics.

Possible signals:

* location
* audience interests
* gender breakdown

---

### Campaign Performance

Track revenue and traffic generated by creators.

Fields may include:

* referral traffic
* sales attributed
* ROI per creator

---
