"""
FatFIRE India — Superintelligence Scraper v2
============================================
Strategy:
  1. Arctic Shift API  → 100% of all posts + comments ever (full history, date-paginated)
  2. Reddit public JSON → real-time top/new/hot (last ~1000, fills recency gap)
  3. Score-weighted     → upvoted content ranked higher in all analysis queries
  4. SQLite + FTS5      → instant full-text search across everything

Arctic Shift: https://arctic-shift.photon-reddit.com
  /api/posts/search?subreddit=fatfireindia&limit=100&sort=asc&after=<ISO date>
  /api/comments/search?subreddit=fatfireindia&limit=100&sort=asc&after=<ISO date>

No API keys needed for either source.
"""

import json
import time
import re
import sqlite3
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
SUBREDDIT        = "fatfireindia"
DATA_DIR         = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH          = DATA_DIR / "fatfireindia.db"

ARCTIC_BASE      = "https://arctic-shift.photon-reddit.com"
REDDIT_BASE      = "https://www.reddit.com"

HEADERS = {"User-Agent": "FatFIRECalc/2.0 (github.com/fatfire-india-calculator; educational)"}

# Earliest known post on r/fatfireindia (sub was created ~2020)
SCRAPE_START     = "2020-01-01"
REQUEST_DELAY    = 1.0   # seconds between requests — be polite


# ── Database ──────────────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id           TEXT PRIMARY KEY,
            title        TEXT NOT NULL DEFAULT '',
            author       TEXT NOT NULL DEFAULT '',
            score        INTEGER NOT NULL DEFAULT 0,
            upvote_ratio REAL DEFAULT 0,
            num_comments INTEGER NOT NULL DEFAULT 0,
            permalink    TEXT NOT NULL DEFAULT '',
            selftext     TEXT NOT NULL DEFAULT '',
            created_utc  INTEGER NOT NULL DEFAULT 0,
            flair        TEXT DEFAULT '',
            source       TEXT DEFAULT '',   -- 'arctic' | 'reddit'
            scraped_at   TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS comments (
            id          TEXT PRIMARY KEY,
            post_id     TEXT NOT NULL,
            parent_id   TEXT NOT NULL DEFAULT '',
            author      TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL DEFAULT '',
            score       INTEGER NOT NULL DEFAULT 0,
            created_utc INTEGER NOT NULL DEFAULT 0,
            depth       INTEGER NOT NULL DEFAULT 0,
            source      TEXT DEFAULT '',
            scraped_at  TEXT NOT NULL DEFAULT ''
        );

        -- Score indexes for ranking
        CREATE INDEX IF NOT EXISTS idx_posts_score   ON posts(score DESC);
        CREATE INDEX IF NOT EXISTS idx_posts_time    ON posts(created_utc);
        CREATE INDEX IF NOT EXISTS idx_comments_score ON comments(score DESC);
        CREATE INDEX IF NOT EXISTS idx_comments_post  ON comments(post_id);

        -- Full-text search
        CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
            title, selftext, author,
            content='posts', content_rowid='rowid'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS comments_fts USING fts5(
            body, author,
            content='comments', content_rowid='rowid'
        );
    """)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_posts(conn: sqlite3.Connection, posts: list[dict], source: str = "arctic"):
    now = _now()
    conn.executemany("""
        INSERT INTO posts (id,title,author,score,upvote_ratio,num_comments,permalink,selftext,created_utc,flair,source,scraped_at)
        VALUES (:id,:title,:author,:score,:upvote_ratio,:num_comments,:permalink,:selftext,:created_utc,:flair,:source,:scraped_at)
        ON CONFLICT(id) DO UPDATE SET
            score        = MAX(score, excluded.score),
            num_comments = MAX(num_comments, excluded.num_comments),
            upvote_ratio = excluded.upvote_ratio,
            scraped_at   = excluded.scraped_at
    """, [
        {
            "id":           p.get("id", ""),
            "title":        p.get("title", "")[:500],
            "author":       p.get("author", ""),
            "score":        p.get("score", 0) or 0,
            "upvote_ratio": p.get("upvote_ratio", 0) or 0,
            "num_comments": p.get("num_comments", 0) or 0,
            "permalink":    p.get("permalink", "") or p.get("url", ""),
            "selftext":     (p.get("selftext", "") or "")[:10000],
            "created_utc":  int(p.get("created_utc", 0) or 0),
            "flair":        p.get("link_flair_text", "") or p.get("flair", "") or "",
            "source":       source,
            "scraped_at":   now,
        }
        for p in posts if p.get("id")
    ])
    conn.commit()


def upsert_comments(conn: sqlite3.Connection, comments: list[dict], source: str = "arctic"):
    now = _now()
    conn.executemany("""
        INSERT INTO comments (id,post_id,parent_id,author,body,score,created_utc,depth,source,scraped_at)
        VALUES (:id,:post_id,:parent_id,:author,:body,:score,:created_utc,:depth,:source,:scraped_at)
        ON CONFLICT(id) DO UPDATE SET
            score      = MAX(score, excluded.score),
            scraped_at = excluded.scraped_at
    """, [
        {
            "id":          c.get("id", ""),
            "post_id":     c.get("link_id", c.get("post_id", "")).replace("t3_", ""),
            "parent_id":   c.get("parent_id", ""),
            "author":      c.get("author", ""),
            "body":        (c.get("body", "") or "")[:5000],
            "score":       c.get("score", 0) or 0,
            "created_utc": int(c.get("created_utc", 0) or 0),
            "depth":       c.get("depth", 0) or 0,
            "source":      source,
            "scraped_at":  now,
        }
        for c in comments
        if c.get("id") and c.get("body") not in (None, "", "[deleted]", "[removed]")
    ])
    conn.commit()


def rebuild_fts(conn: sqlite3.Connection):
    """Rebuild FTS index after bulk inserts."""
    conn.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO comments_fts(comments_fts) VALUES('rebuild')")
    conn.commit()


# ── Arctic Shift scraper (100% historical) ────────────────────────────────────
class ArcticShiftScraper:
    """
    Paginates through ALL posts/comments using after= date-based pagination.
    Fetches everything from SCRAPE_START to now, never misses a post.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn    = conn
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get(self, endpoint: str, params: dict) -> Optional[dict]:
        url = f"{ARCTIC_BASE}{endpoint}"
        for attempt in range(4):
            try:
                r = self.session.get(url, params=params, timeout=30)
                if r.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f"  [arctic] rate-limited, waiting {wait}s…")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                wait = 2 ** attempt
                print(f"  [arctic] error ({e}), retry in {wait}s…")
                time.sleep(wait)
        return None

    def scrape_posts(self, progress_cb=None) -> int:
        """Fetch every post ever on r/fatfireindia."""
        print(f"[arctic] Scraping ALL posts from r/{SUBREDDIT} since {SCRAPE_START}…")
        after    = SCRAPE_START
        total    = 0
        page     = 0

        while True:
            data = self._get("/api/posts/search", {
                "subreddit": SUBREDDIT,
                "limit":     100,
                "sort":      "asc",
                "after":     after,
            })
            if not data:
                print("[arctic] Empty response — stopping post scrape")
                break

            items = data.get("data", [])
            if not items:
                break

            upsert_posts(self.conn, items, source="arctic")
            total += len(items)
            page  += 1

            # Advance cursor to last item's created_utc
            last_ts = items[-1].get("created_utc", 0)
            after   = datetime.fromtimestamp(int(last_ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

            print(f"  [arctic] posts page {page}: +{len(items)} (total={total}, cursor={after})")
            if progress_cb:
                progress_cb({"stage": "posts", "total": total, "cursor": after})

            if len(items) < 100:
                # Last page
                break

            time.sleep(REQUEST_DELAY)

        print(f"[arctic] Posts done: {total} total")
        return total

    def scrape_comments(self, progress_cb=None) -> int:
        """Fetch every comment ever on r/fatfireindia."""
        print(f"[arctic] Scraping ALL comments from r/{SUBREDDIT} since {SCRAPE_START}…")
        after = SCRAPE_START
        total = 0
        page  = 0

        while True:
            data = self._get("/api/comments/search", {
                "subreddit": SUBREDDIT,
                "limit":     100,
                "sort":      "asc",
                "after":     after,
            })
            if not data:
                break

            items = data.get("data", [])
            if not items:
                break

            upsert_comments(self.conn, items, source="arctic")
            total += len(items)
            page  += 1

            last_ts = items[-1].get("created_utc", 0)
            after   = datetime.fromtimestamp(int(last_ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

            print(f"  [arctic] comments page {page}: +{len(items)} (total={total}, cursor={after})")
            if progress_cb:
                progress_cb({"stage": "comments", "total": total, "cursor": after})

            if len(items) < 100:
                break

            time.sleep(REQUEST_DELAY)

        print(f"[arctic] Comments done: {total} total")
        return total


# ── Reddit public JSON (real-time top layer) ───────────────────────────────────
class RedditPublicScraper:
    """
    Tops up with recent/live data via Reddit's public .json endpoints.
    Fills the gap between Arctic Shift's last update and now.
    Max ~1000 posts per sort — but that's fine as a real-time supplement.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn    = conn
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get(self, url: str, params: dict) -> Optional[dict]:
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, timeout=15)
                if r.status_code == 429:
                    time.sleep(10)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                time.sleep(2 ** attempt)
        return None

    def _fetch_listing(self, sort: str, time_filter: str = "all") -> list[dict]:
        url    = f"{REDDIT_BASE}/r/{SUBREDDIT}/{sort}.json"
        posts  = []
        after  = None

        while True:
            params = {"limit": 100, "t": time_filter, "raw_json": 1}
            if after:
                params["after"] = after
            data = self._get(url, params)
            if not data:
                break
            children = data.get("data", {}).get("children", [])
            batch    = [c["data"] for c in children if c.get("kind") == "t3"]
            posts.extend(batch)
            after = data["data"].get("after")
            if not after or len(batch) < 100:
                break
            time.sleep(REQUEST_DELAY)

        return posts

    def _flatten_comments(self, children: list, post_id: str, depth: int = 0) -> list[dict]:
        result = []
        for child in children:
            if child.get("kind") != "t1":
                continue
            d    = child["data"]
            body = d.get("body", "")
            if not body or body in ("[deleted]", "[removed]"):
                continue
            result.append({
                "id":          d.get("id", ""),
                "post_id":     post_id,
                "parent_id":   d.get("parent_id", ""),
                "author":      d.get("author", ""),
                "body":        body,
                "score":       d.get("score", 0) or 0,
                "created_utc": d.get("created_utc", 0) or 0,
                "depth":       depth,
            })
            replies = d.get("replies", {})
            if replies and isinstance(replies, dict):
                sub = replies.get("data", {}).get("children", [])
                result.extend(self._flatten_comments(sub, post_id, depth + 1))
        return result

    def scrape_top(self, progress_cb=None) -> tuple[int, int]:
        total_posts = total_comments = 0
        for sort in ["top", "new", "hot", "rising"]:
            posts = self._fetch_listing(sort)
            if posts:
                upsert_posts(self.conn, posts, source="reddit")
                total_posts += len(posts)
                print(f"  [reddit] {sort}: {len(posts)} posts")
                if progress_cb:
                    progress_cb({"stage": f"reddit_{sort}", "total": total_posts})

        # Fetch full comment trees for top-scored posts
        cur = self.conn.execute(
            "SELECT id, permalink FROM posts WHERE source='reddit' ORDER BY score DESC LIMIT 300"
        )
        posts_for_comments = cur.fetchall()
        for i, (pid, permalink) in enumerate(posts_for_comments):
            if not permalink:
                continue
            url = f"{REDDIT_BASE}{permalink}.json"
            try:
                r = self.session.get(url, params={"limit": 500, "depth": 10, "raw_json": 1}, timeout=15)
                if r.status_code != 200:
                    continue
                data = r.json()
                if len(data) >= 2:
                    raw = data[1]["data"]["children"]
                    flat = self._flatten_comments(raw, pid)
                    upsert_comments(self.conn, flat, source="reddit")
                    total_comments += len(flat)
            except Exception:
                pass
            if (i + 1) % 20 == 0:
                print(f"  [reddit] comment trees: {i+1}/{len(posts_for_comments)}")
                if progress_cb:
                    progress_cb({"stage": "reddit_comments", "total": total_comments})
            time.sleep(REQUEST_DELAY)

        return total_posts, total_comments


# ── Main orchestrator ─────────────────────────────────────────────────────────
def scrape(progress_cb=None, skip_arctic: bool = False) -> dict:
    """
    Full scrape:
      1. Arctic Shift — all historical posts
      2. Arctic Shift — all historical comments
      3. Reddit JSON  — real-time top layer
      4. FTS rebuild
    Returns stats dict.
    """
    conn = init_db()
    stats = {"posts": 0, "comments": 0, "errors": []}

    if not skip_arctic:
        arctic = ArcticShiftScraper(conn)
        try:
            stats["posts"]    += arctic.scrape_posts(progress_cb)
            stats["comments"] += arctic.scrape_comments(progress_cb)
        except Exception as e:
            msg = f"Arctic Shift error: {e}"
            print(f"[scraper] {msg}")
            stats["errors"].append(msg)

    # Always top-up with Reddit public JSON
    reddit = RedditPublicScraper(conn)
    try:
        rp, rc = reddit.scrape_top(progress_cb)
        stats["posts"]    += rp
        stats["comments"] += rc
    except Exception as e:
        msg = f"Reddit JSON error: {e}"
        print(f"[scraper] {msg}")
        stats["errors"].append(msg)

    print("[scraper] Rebuilding FTS indexes…")
    rebuild_fts(conn)
    conn.close()

    print(f"[scraper] Done — posts={stats['posts']}, comments={stats['comments']}")
    return stats


# ── Analysis: score-weighted community intelligence ───────────────────────────
def extract_numbers(text: str) -> list[float]:
    text = text.lower()
    amounts = []
    for v in re.findall(r'(\d+(?:\.\d+)?)\s*(?:cr|crore)', text):
        amounts.append(float(v) * 1e7)
    for v in re.findall(r'(\d+(?:\.\d+)?)\s*(?:l|lakh|lakhs)', text):
        amounts.append(float(v) * 1e5)
    return amounts


def get_community_insights() -> dict:
    """
    Score-weighted analysis of all scraped data.
    Comments/posts with higher upvotes count more toward all statistics.
    """
    if not DB_PATH.exists():
        return {}

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    c.execute("SELECT COUNT(*) FROM posts")
    post_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM comments")
    comment_count = c.fetchone()[0]

    if post_count == 0 and comment_count == 0:
        conn.close()
        return {}

    # ── Score-weighted text corpus ──────────────────────────────────────────
    # Pull comments sorted by score descending — higher score = more weight
    # We repeat each comment proportional to its score band for weighted stats
    c.execute("""
        SELECT body, score FROM comments
        WHERE score >= 1
          AND length(body) > 20
        ORDER BY score DESC
        LIMIT 10000
    """)
    comment_rows = c.fetchall()

    c.execute("""
        SELECT title || ' ' || selftext, score FROM posts
        WHERE score >= 1
        ORDER BY score DESC
        LIMIT 3000
    """)
    post_rows = c.fetchall()

    # Build weighted corpus: each item appears ceil(log2(score+2)) times
    import math
    def weight(score):
        return max(1, int(math.log2(score + 2)))

    weighted_texts = []
    for body, score in comment_rows:
        weighted_texts.extend([body] * weight(score))
    for text, score in post_rows:
        weighted_texts.extend([text] * weight(score))

    # ── Extract corpus targets ──────────────────────────────────────────────
    corpus_in_cr = []
    for text in weighted_texts:
        for n in extract_numbers(text):
            cr = n / 1e7
            if 1 <= cr <= 200:
                corpus_in_cr.append(cr)

    # ── SWR ────────────────────────────────────────────────────────────────
    swr_values = []
    for text in weighted_texts:
        for v in re.findall(r'(\d+(?:\.\d+)?)\s*%?\s*(?:swr|safe.?withdrawal)', text.lower()):
            fv = float(v)
            if 1.0 <= fv <= 6.0:
                swr_values.append(fv)

    # ── Retire ages ─────────────────────────────────────────────────────────
    retire_ages = []
    for text in weighted_texts:
        for v in re.findall(r'(?:retire[d]?|retiring)\s+(?:at|by|@)\s*(\d{2})', text.lower()):
            age = int(v)
            if 25 <= age <= 70:
                retire_ages.append(age)

    # ── Monthly expenses ────────────────────────────────────────────────────
    monthly_expenses = []
    for text in weighted_texts:
        t = text.lower()
        pats = re.findall(r'(\d+(?:\.\d+)?)\s*(?:l|lakh)\s*(?:per\s*month|/month|pm|monthly)', t)
        pats += re.findall(r'monthly\s+expense[s]?\s+(?:is|of|around|~)?\s*(\d+(?:\.\d+)?)\s*(?:l|lakh)', t)
        for v in pats:
            val = float(v) * 1e5
            if 20000 <= val <= 1e7:
                monthly_expenses.append(val)

    # ── Asset allocation mentions ───────────────────────────────────────────
    equity_pcts = []
    for text in weighted_texts:
        for v in re.findall(r'(\d{2})\s*%\s*(?:equity|stocks?|nifty)', text.lower()):
            pct = int(v)
            if 30 <= pct <= 100:
                equity_pcts.append(pct)

    # ── Top mentioned investment vehicles ───────────────────────────────────
    vehicles = {
        "nifty_index": 0, "ppf": 0, "epf": 0, "nps": 0,
        "sip": 0, "real_estate": 0, "gold": 0, "fd": 0,
        "us_stocks": 0, "smallcase": 0,
    }
    kw_map = {
        "nifty_index": ["nifty index", "index fund", "nifty 50"],
        "ppf": ["ppf"],
        "epf": ["epf", "vpf"],
        "nps": ["nps"],
        "sip": ["sip"],
        "real_estate": ["real estate", "property", "flat", "apartment"],
        "gold": ["gold", "sgb", "sovereign gold"],
        "fd": ["fd", "fixed deposit"],
        "us_stocks": ["us stocks", "s&p", "nasdaq", "vtsax", "international"],
        "smallcase": ["smallcase"],
    }
    for text in weighted_texts:
        tl = text.lower()
        for key, kws in kw_map.items():
            if any(kw in tl for kw in kws):
                vehicles[key] += 1

    conn.close()

    def median(lst):
        if not lst:
            return None
        s = sorted(lst)
        return s[len(s) // 2]

    def pct(lst, p):
        if not lst:
            return None
        s = sorted(lst)
        return s[int(len(s) * p / 100)]

    return {
        "post_count":              post_count,
        "comment_count":           comment_count,
        "corpus_mentions_count":   len(corpus_in_cr),
        "median_corpus_cr":        round(median(corpus_in_cr), 1) if corpus_in_cr else None,
        "p25_corpus_cr":           round(pct(corpus_in_cr, 25), 1) if corpus_in_cr else None,
        "p75_corpus_cr":           round(pct(corpus_in_cr, 75), 1) if corpus_in_cr else None,
        "avg_corpus_cr":           round(sum(corpus_in_cr)/len(corpus_in_cr), 1) if corpus_in_cr else None,
        "median_swr_pct":          round(median(swr_values), 2) if swr_values else 3.5,
        "median_retire_age":       median(retire_ages) or 45,
        "median_monthly_expense":  median(monthly_expenses),
        "median_equity_pct":       median(equity_pcts),
        "popular_corpus_targets":  sorted(set(round(x) for x in corpus_in_cr if x >= 1))[:15],
        "top_vehicles":            dict(sorted(vehicles.items(), key=lambda x: -x[1])[:6]),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-arctic", action="store_true", help="Only scrape Reddit JSON (faster, less complete)")
    args = parser.parse_args()
    stats = scrape(skip_arctic=args.skip_arctic)
    print(json.dumps(stats, indent=2))
    insights = get_community_insights()
    print(json.dumps(insights, indent=2, default=str))
