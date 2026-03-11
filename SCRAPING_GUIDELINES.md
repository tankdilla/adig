# SCRAPING_GUIDELINES.md

Safe Scraping Practices for Instagram

---

# Purpose

This document outlines safe scraping practices for the creator discovery system.

The goal is to collect publicly available creator data **without triggering platform enforcement mechanisms**.

Scraping should mimic normal browsing behavior.

---

# Key Principles

### 1. Limit Request Volume

High request volume is the most common trigger for detection.

Recommended limits:

| Resource      | Delay         |
| ------------- | ------------- |
| Hashtag pages | 10–30 seconds |
| Profile pages | 10–30 seconds |
| Post pages    | 5–15 seconds  |

Random delays should always be added.

---

### 2. Use Randomized Timing

Avoid predictable request patterns.

Example:

```
sleep(random between 3 and 8 seconds)
```

This simulates human browsing behavior.

---

### 3. Avoid 24/7 Crawling

Scraping should run periodically.

Recommended schedule:

| Task            | Frequency       |
| --------------- | --------------- |
| Discovery       | 2–4 times daily |
| Intel updates   | daily           |
| Graph expansion | daily           |

Continuous scraping increases risk.

---

### 4. Reuse Browser Sessions

Launching a fresh browser for every request may look suspicious.

The system should reuse:

* Playwright browser instance
* browser context
* session cookies

This reduces “new device” signals.

---

### 5. Use a Dedicated Scraper Account

Never scrape using the main brand account.

Instead:

* create a separate Instagram account
* log in manually
* store session state
* reuse the session for scraping

If enforcement occurs, the brand account remains safe.

---

### 6. Store Session State Securely

The scraper may use:

```
shared/ig_storage_state.json
```

This file contains authentication tokens.

Security precautions:

* never commit to Git
* store locally only
* restrict access

Add to `.gitignore`:

```
shared/ig_storage_state.json
```

---

### 7. Monitor Rate Limiting Signals

Instagram sometimes displays messages like:

```
Please wait a few minutes before trying again
```

When detected:

* immediately stop scraping
* pause for 30–120 minutes
* reduce request volume

Ignoring throttling signals increases risk.

---

### 8. Avoid Automation Actions

Scraping should only read public content.

Avoid automated actions such as:

* following accounts
* liking posts
* sending messages
* posting comments

These actions dramatically increase detection risk.

---

### 9. Cache Scraped Pages

To reduce repeated requests:

* cache HTML responses
* store discovered creators
* avoid revisiting the same profiles frequently

Caching reduces network load and risk.

---

### 10. Monitor Scraper Health

Track metrics such as:

* login wall frequency
* error rates
* request counts

These metrics help detect when scraping behavior becomes unsafe.

---

# Emergency Recovery Plan

If Instagram blocks scraping:

1. Stop scraping immediately
2. Wait several hours or days
3. Regenerate session storage
4. Reduce scraping frequency
5. Resume slowly

---

# Ethical Considerations

The system only collects publicly visible information.

It should not:

* access private content
* bypass platform security
* collect sensitive personal data

The goal is to support **authentic creator collaborations**, not invasive data collection.

---

# Long-Term Strategy

As the creator database grows, the system should rely more on:

* creator graph relationships
* audience seeding
* historical intelligence signals

This reduces the need for aggressive scraping.
