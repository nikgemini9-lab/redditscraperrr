"""
FatFIRE India — Intelligence Layer
===================================
Uses Claude claude-opus-4-6 with adaptive thinking to:
  1. Pull the most relevant r/fatfireindia posts/comments for a user's situation
  2. Stream a personalized FIRE analysis grounded in real community data
  3. Extract structured community consensus from scraped posts (batch, cached)

Requires: ANTHROPIC_API_KEY environment variable
"""

import os
import sqlite3
import json
import re
from typing import Generator, Optional
import anthropic

from scraper import DB_PATH

client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env


# ── Retrieval: find relevant community posts ──────────────────────────────────

def get_relevant_community_data(user_data: dict, n_posts: int = 8, n_comments: int = 12) -> tuple[list, list]:
    """
    Score-ranked retrieval of community posts/comments relevant to the user.
    Uses keyword overlap + score weighting — no vector DB needed.
    """
    if not DB_PATH.exists():
        return [], []

    age    = user_data.get("current_age", 35)
    income = user_data.get("monthly_income", 200000)
    ftype  = user_data.get("fire_type", "fat")

    # Build keyword set from user's situation
    keywords = [ftype + "fire", "fatfire", "fire number", "corpus"]
    if age < 35:
        keywords += ["early 30", "30s", "young"]
    elif age < 45:
        keywords += ["late 30", "40s", "mid career"]
    else:
        keywords += ["50s", "late career", "nearing retirement"]

    if income > 500000:
        keywords += ["high income", "senior", "director", "vp", "crore"]
    elif income > 200000:
        keywords += ["10 lakh", "20 lakh", "15 lakh"]

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()

    c.execute("""
        SELECT title, selftext, score, author
        FROM posts
        WHERE score >= 3 AND length(selftext) > 80
        ORDER BY score DESC
        LIMIT 120
    """)
    all_posts = [{"title": r[0], "text": r[1], "score": r[2], "author": r[3]} for r in c.fetchall()]

    c.execute("""
        SELECT body, score, author
        FROM comments
        WHERE score >= 5 AND length(body) > 60
        ORDER BY score DESC
        LIMIT 200
    """)
    all_comments = [{"text": r[0], "score": r[1], "author": r[2]} for r in c.fetchall()]

    conn.close()

    def relevance(text: str, score: int) -> float:
        tl = text.lower()
        kw_hits = sum(1 for kw in keywords if kw in tl)
        fire_hits = sum(1 for kw in ["corpus", "crore", "lakh", "retire", "swr", "withdrawal"] if kw in tl)
        return score * 0.4 + kw_hits * 3 + fire_hits * 2

    posts_scored = sorted(
        all_posts,
        key=lambda p: relevance(p["title"] + " " + p["text"], p["score"]),
        reverse=True,
    )
    comments_scored = sorted(
        all_comments,
        key=lambda c: relevance(c["text"], c["score"]),
        reverse=True,
    )

    return posts_scored[:n_posts], comments_scored[:n_comments]


# ── Streaming analysis ────────────────────────────────────────────────────────

def stream_fire_analysis(
    user_data: dict,
    fire_results: dict,
    posts: list,
    comments: list,
) -> Generator[str, None, None]:
    """
    Streams a personalized FIRE analysis using Claude claude-opus-4-6.
    Yields text chunks as they arrive.
    """

    # ── Build community context ──────────────────────────────────────────────
    community_ctx = ""
    if posts:
        community_ctx += "HIGHEST-UPVOTED POSTS FROM r/fatfireindia:\n"
        for i, p in enumerate(posts[:6], 1):
            body = (p.get("text") or "")[:500].strip()
            community_ctx += f"\n[Post #{i} | score={p['score']}]\n{p['title']}\n{body}\n"
    if comments:
        community_ctx += "\n\nMOST UPVOTED COMMENTS:\n"
        for i, c in enumerate(comments[:8], 1):
            community_ctx += f"\n[Comment #{i} | score={c['score']}]\n{c['text'][:350]}\n"

    has_community = bool(posts or comments)

    # ── User situation summary ───────────────────────────────────────────────
    current_age    = user_data.get("current_age", 32)
    retire_age     = user_data.get("target_retire_age", 45)
    years_left     = retire_age - current_age
    monthly_income = user_data.get("monthly_income", 0)
    monthly_exp    = user_data.get("target_monthly_expense_today", 0)
    savings_rate   = round((monthly_income - monthly_exp) / monthly_income * 100) if monthly_income else 0
    total_current  = (
        user_data.get("current_equity", 0) + user_data.get("current_debt", 0)
        + user_data.get("current_epf", 0) + user_data.get("current_ppf", 0)
        + user_data.get("current_nps", 0) + user_data.get("current_gold", 0)
        + user_data.get("current_cash", 0)
    )

    user_ctx = f"""
PERSON'S FINANCIAL SNAPSHOT:
• Age: {current_age}, wants to retire at {retire_age} ({years_left} years left)
• Monthly income: ₹{monthly_income/1e5:.1f}L | Expenses: ₹{monthly_exp/1e5:.1f}L | Savings rate: {savings_rate}%
• Current portfolio: ₹{total_current/1e7:.2f} Cr total
  (Equity ₹{user_data.get('current_equity',0)/1e7:.2f}Cr, EPF ₹{user_data.get('current_epf',0)/1e7:.2f}Cr, Debt ₹{user_data.get('current_debt',0)/1e7:.2f}Cr)
• Monthly investments: ₹{user_data.get('monthly_equity_sip',0)/1000:.0f}K equity SIP, ₹{user_data.get('monthly_epf_employee',0)/1000:.0f}K EPF
• FIRE type: {user_data.get('fire_type','fat').upper()} FIRE

CALCULATOR OUTPUT:
• FIRE corpus needed: {fire_results.get('fire_corpus_required_fmt','—')}
• Projected corpus at {retire_age}: {fire_results.get('projected_corpus_fmt','—')}
• Status: {'✅ ON TRACK' if fire_results.get('on_track') else '⚠️ BEHIND — needs action'}
• Gap / surplus: {fire_results.get('corpus_gap_fmt','—')} {'(surplus)' if fire_results.get('on_track') else '(shortfall)'}
• Earliest FIRE age: {fire_results.get('projected_fire_age','—')}
• Monthly withdrawal at retirement: {fire_results.get('monthly_withdrawal','—')}
• Corpus survives full life ({user_data.get('life_expectancy',85)}): {'Yes' if fire_results.get('corpus_survives') else 'No — depletes in ' + str(fire_results.get('corpus_lasts_years','?')) + ' yrs'}
• Coast FIRE: {'Already achieved! 🏖️' if fire_results.get('coast_fire_achievable') else 'Not yet (need ' + fire_results.get('coast_fire_corpus','—') + ')'}
"""

    # ── System prompt ────────────────────────────────────────────────────────
    system = f"""You are the most trusted FIRE advisor in India, deeply embedded in the r/fatfireindia community. \
You have personally analyzed thousands of posts from real Indians pursuing financial independence.

Your voice: sharp, warm, direct. Like a brilliant friend — not a corporate consultant. \
You speak to THIS person's exact numbers. You reference what the community actually says \
about situations like theirs. You celebrate what's working and are unflinching about risks.

{'You have access to actual r/fatfireindia community posts and comments below — use them.' if has_community else 'No community data is loaded yet — base your analysis on general India FIRE principles.'}

FORMAT: Write 3 short paragraphs in flowing prose. No bullet points. No headers. No asterisks.
— Para 1: Honest read of their situation (2-3 sentences)
— Para 2: What the r/fatfireindia community would say about this (reference specific wisdom)
— Para 3: The 2 most important actions to take right now (specific, actionable)
Total: ~250-300 words max. Every sentence must earn its place."""

    messages = [
        {
            "role": "user",
            "content": user_ctx + (
                f"\n\nCOMMUNITY CONTEXT:\n{community_ctx}" if has_community else ""
            ) + "\n\nGive me your honest analysis.",
        }
    ]

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=512,
        thinking={"type": "adaptive"},
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


# ── Async wrapper for FastAPI ─────────────────────────────────────────────────

async def async_stream_analysis(user_data: dict, fire_results: dict):
    """
    Async generator that wraps the sync streaming call.
    Yields SSE-formatted data chunks.
    """
    import asyncio

    loop   = asyncio.get_event_loop()
    posts, comments = await loop.run_in_executor(
        None, lambda: get_relevant_community_data(user_data)
    )

    # Run sync stream in executor, yield chunks
    queue: asyncio.Queue = asyncio.Queue()
    done_sentinel = object()

    def run_stream():
        try:
            for chunk in stream_fire_analysis(user_data, fire_results, posts, comments):
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done_sentinel)

    loop.run_in_executor(None, run_stream)

    while True:
        item = await queue.get()
        if item is done_sentinel:
            break
        yield item


# ── Standalone: extract structured consensus (batch, for /community-insights) ─

def extract_consensus_from_top_posts(limit: int = 30) -> Optional[dict]:
    """
    Uses Claude to extract structured community consensus from top posts.
    Call this after a scrape to enrich the insights panel.
    """
    if not DB_PATH.exists():
        return None

    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        SELECT title, selftext, score FROM posts
        WHERE score >= 10 AND length(selftext) > 100
        ORDER BY score DESC LIMIT ?
    """, (limit,))
    posts = c.fetchall()
    conn.close()

    if not posts:
        return None

    content = "\n\n".join(
        f"[Score:{p[2]}] {p[0]}\n{p[1][:600]}" for p in posts
    )

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        system="You are a financial data analyst. Extract structured insights from r/fatfireindia posts. Return ONLY valid JSON.",
        messages=[{
            "role": "user",
            "content": f"""From these top r/fatfireindia posts, extract community consensus. Return JSON only:
{{
  "consensus_swr_pct": <number, safe withdrawal rate percentage the community agrees on>,
  "consensus_retire_age": <number, most common target retire age>,
  "fat_fire_min_cr": <number, minimum corpus in crores for fat fire>,
  "top_advice": [<3 short strings, most repeated pieces of advice>],
  "common_mistakes": [<2 short strings, mistakes community warns against>]
}}

POSTS:
{content[:8000]}"""
        }]
    )

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    try:
        # Extract JSON even if wrapped in markdown
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else None
    except Exception:
        return None
