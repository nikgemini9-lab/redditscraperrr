# 🔥 FatFIRE India Calculator

> Comprehensive FIRE calculator for India, powered by community wisdom from r/fatfireindia.

Built by scraping and analyzing thousands of posts and comments from r/fatfireindia to extract:
- Median corpus targets discussed by the community
- Safe Withdrawal Rate consensus (India uses 3-3.5%, not 4%)
- Retirement age benchmarks
- Asset allocation strategies
- Expense benchmarks for metro vs Tier-2 cities

## Features

- **Live Reddit scraper** — fetches posts & comments from r/fatfireindia using public JSON API (no credentials)
- **India-specific FIRE math** — 6.5% inflation, 13% Nifty returns, EPF/PPF/NPS support
- **4 FIRE types** — Lean, Regular, Fat, Barista FIRE
- **Coast FIRE calculator** — know if your corpus can compound to target without more saving
- **Tax calculator** — New Regime FY25, LTCG estimation
- **Community insights** — benchmarks derived from real r/fatfireindia discussions
- **Beautiful UI** — dark mode, interactive charts, responsive

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:8000

## Scrape Fresh Data

Hit the "Scrape Reddit Now" button in the UI, or:

```bash
python scraper.py
```

## Key India FIRE Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| Inflation | 6.5% | India CPI historical avg |
| Equity returns | 13% | Nifty 50 long-term CAGR |
| Safe Withdrawal Rate | 3.5% | r/fatfireindia consensus |
| EPF rate | 8.15% | EPFO 2023-24 |
| PPF rate | 7.1% | Current govt rate |
| Lean FIRE min | ₹1-3 Cr | Community benchmark |
| Fat FIRE min | ₹10 Cr+ | Community benchmark |

## Architecture

```
app.py              — FastAPI web server
scraper.py          — Reddit public API scraper
fire_calculator.py  — FIRE math engine
templates/
  index.html        — Single-page web app
data/
  fatfireindia.db   — SQLite database (scraped data)
```

## Inspired by

- [reddit-universal-scraper](https://github.com/ksanjeev284/reddit-universal-scraper)
- r/fatfireindia community
- The Trinity Study (adapted for India)
