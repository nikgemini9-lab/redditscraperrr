"""
Reddit scraper for r/fatfireindia using public JSON API (no credentials needed).
Inspired by: https://github.com/ksanjeev284/reddit-universal-scraper
"""
import json
import time
import re
import os
import sqlite3
import requests
from datetime import datetime
from pathlib import Path

SUBREDDIT = "fatfireindia"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "fatfireindia.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 FatFIREIndia-Calculator/1.0 (educational project)"
}

FIRE_KEYWORDS = [
    "corpus", "crore", "lakh", "retire", "retirement", "swr", "withdrawal",
    "fire number", "net worth", "portfolio", "mutual fund", "nifty", "sensex",
    "epf", "ppf", "nps", "sip", "equity", "debt", "real estate", "gold",
    "inflation", "expense", "monthly expense", "annual expense", "age",
    "coastfire", "baristafire", "fatfire", "leanfire", "fi", "independence",
    "passive income", "dividend", "rental income", "safe withdrawal",
    "asset allocation", "rebalancing", "healthcare", "insurance",
]

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            title TEXT,
            author TEXT,
            score INTEGER,
            num_comments INTEGER,
            url TEXT,
            selftext TEXT,
            created_utc REAL,
            flair TEXT,
            scraped_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY,
            post_id TEXT,
            author TEXT,
            body TEXT,
            score INTEGER,
            created_utc REAL,
            depth INTEGER,
            scraped_at TEXT
        )
    """)
    conn.commit()
    return conn


def fetch_posts(limit=100, after=None, time_filter="all", sort="top"):
    url = f"https://www.reddit.com/r/{SUBREDDIT}/{sort}.json"
    params = {"limit": limit, "t": time_filter, "raw_json": 1}
    if after:
        params["after"] = after
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[scraper] Error fetching posts: {e}")
        return None


def fetch_comments(post_id, post_url):
    url = f"https://www.reddit.com{post_url}.json"
    params = {"limit": 500, "depth": 10, "raw_json": 1}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if len(data) >= 2:
            return data[1]["data"]["children"]
        return []
    except Exception as e:
        print(f"[scraper] Error fetching comments for {post_id}: {e}")
        return []


def flatten_comments(children, post_id, depth=0):
    result = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        d = child["data"]
        body = d.get("body", "")
        if not body or body in ("[deleted]", "[removed]"):
            continue
        result.append({
            "id": d.get("id", ""),
            "post_id": post_id,
            "author": d.get("author", ""),
            "body": body,
            "score": d.get("score", 0),
            "created_utc": d.get("created_utc", 0),
            "depth": depth,
        })
        replies = d.get("replies", {})
        if replies and isinstance(replies, dict):
            sub = replies.get("data", {}).get("children", [])
            result.extend(flatten_comments(sub, post_id, depth + 1))
    return result


def save_posts(conn, posts):
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    for p in posts:
        d = p["data"]
        c.execute("""
            INSERT OR REPLACE INTO posts
            (id, title, author, score, num_comments, url, selftext, created_utc, flair, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            d.get("id"),
            d.get("title", ""),
            d.get("author", ""),
            d.get("score", 0),
            d.get("num_comments", 0),
            d.get("permalink", ""),
            d.get("selftext", ""),
            d.get("created_utc", 0),
            d.get("link_flair_text", ""),
            now,
        ))
    conn.commit()


def save_comments(conn, comments):
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    for cm in comments:
        c.execute("""
            INSERT OR REPLACE INTO comments
            (id, post_id, author, body, score, created_utc, depth, scraped_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            cm["id"], cm["post_id"], cm["author"], cm["body"],
            cm["score"], cm["created_utc"], cm["depth"], now,
        ))
    conn.commit()


def scrape(max_posts=500, delay=1.5):
    conn = init_db()
    print(f"[scraper] Starting scrape of r/{SUBREDDIT}")
    all_posts = []
    after = None
    fetched = 0

    for sort in ["top", "new", "hot"]:
        after = None
        while fetched < max_posts:
            data = fetch_posts(limit=100, after=after, sort=sort)
            if not data:
                break
            children = data.get("data", {}).get("children", [])
            if not children:
                break
            posts = [c for c in children if c.get("kind") == "t3"]
            save_posts(conn, posts)
            all_posts.extend(posts)
            fetched += len(posts)
            after = data["data"].get("after")
            print(f"[scraper] Fetched {fetched} posts (sort={sort})")
            if not after:
                break
            time.sleep(delay)

    print(f"[scraper] Scraping comments for {len(all_posts)} posts...")
    for i, post in enumerate(all_posts[:200]):
        d = post["data"]
        post_id = d.get("id")
        permalink = d.get("permalink", "")
        if not permalink:
            continue
        comments = fetch_comments(post_id, permalink)
        flat = flatten_comments(comments, post_id)
        save_comments(conn, flat)
        if (i + 1) % 10 == 0:
            print(f"[scraper] Processed comments: {i+1}/{min(200, len(all_posts))}")
        time.sleep(delay)

    conn.close()
    print(f"[scraper] Done. Data saved to {DB_PATH}")
    return str(DB_PATH)


def extract_numbers(text):
    """Extract mentions of crore/lakh amounts from text."""
    text = text.lower()
    amounts = []
    crore_pat = re.findall(r'(\d+(?:\.\d+)?)\s*(?:cr|crore)', text)
    lakh_pat = re.findall(r'(\d+(?:\.\d+)?)\s*(?:l|lakh|lakhs)', text)
    for v in crore_pat:
        amounts.append(float(v) * 1e7)
    for v in lakh_pat:
        amounts.append(float(v) * 1e5)
    return amounts


def get_community_insights():
    """Analyze scraped data to extract community wisdom."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM posts")
    post_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM comments")
    comment_count = c.fetchone()[0]

    # Corpus amounts mentioned
    all_text = []
    c.execute("SELECT body FROM comments WHERE score > 2 ORDER BY score DESC LIMIT 2000")
    for row in c.fetchall():
        all_text.append(row[0])
    c.execute("SELECT selftext, title FROM posts WHERE score > 5 LIMIT 500")
    for row in c.fetchall():
        all_text.append(row[0] + " " + row[1])

    corpus_mentions = []
    for text in all_text:
        nums = extract_numbers(text)
        corpus_mentions.extend(nums)

    corpus_in_cr = [n / 1e7 for n in corpus_mentions if 1e7 <= n <= 1e9]  # 1Cr to 100Cr

    # SWR mentions
    swr_values = []
    for text in all_text:
        swrs = re.findall(r'(\d+(?:\.\d+)?)\s*%\s*(?:swr|safe withdrawal|withdrawal rate)', text.lower())
        swr_values.extend([float(v) for v in swrs if 1 <= float(v) <= 6])

    # Age mentions with retire
    retire_ages = []
    for text in all_text:
        ages = re.findall(r'(?:retire|retired|retiring)\s+(?:at|by|@)\s*(\d{2})', text.lower())
        retire_ages.extend([int(a) for a in ages if 25 <= int(a) <= 70])

    monthly_expenses = []
    for text in all_text:
        t = text.lower()
        exps = re.findall(r'(\d+(?:\.\d+)?)\s*(?:l|lakh|lakhs)\s*(?:per|/)\s*(?:month|monthly|mo|yr|year)', t)
        exps2 = re.findall(r'monthly\s+expense[s]?\s+(?:is|of|around|~)?\s*(\d+(?:\.\d+)?)\s*(?:l|lakh)', t)
        for v in exps + exps2:
            val = float(v) * 1e5
            if 10000 <= val <= 5e6:
                monthly_expenses.append(val)

    conn.close()

    def median(lst):
        if not lst:
            return None
        s = sorted(lst)
        n = len(s)
        return s[n // 2]

    def avg(lst):
        return sum(lst) / len(lst) if lst else None

    return {
        "post_count": post_count,
        "comment_count": comment_count,
        "corpus_mentions_count": len(corpus_in_cr),
        "median_corpus_cr": round(median(corpus_in_cr), 1) if corpus_in_cr else 10,
        "avg_corpus_cr": round(avg(corpus_in_cr), 1) if corpus_in_cr else 10,
        "median_swr_pct": round(median(swr_values), 1) if swr_values else 3.5,
        "median_retire_age": median(retire_ages) or 45,
        "median_monthly_expense": median(monthly_expenses),
        "popular_corpus_targets": sorted(set([round(x) for x in corpus_in_cr if x >= 2]), )[:20],
    }


if __name__ == "__main__":
    scrape(max_posts=300)
    insights = get_community_insights()
    print(json.dumps(insights, indent=2))
