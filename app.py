"""
FatFIRE India Web Calculator
FastAPI app powered by r/fatfireindia community wisdom + Claude AI.
"""
import json
import asyncio
import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from fire_calculator import FIREInputs, FIREResults, calculate_fire, format_inr, get_fire_benchmarks
from scraper import get_community_insights, DB_PATH

app = FastAPI(title="FatFIRE India Calculator", version="1.0.0")

templates = Jinja2Templates(directory="templates")
static_path = Path("static")
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

_scrape_status = {
    "running": False, "done": False,
    "post_count": 0, "comment_count": 0,
    "stage": "", "error": None,
}


class CalculatorRequest(BaseModel):
    current_age: int = 32
    target_retire_age: int = 45
    life_expectancy: int = 85
    monthly_income: float = 300000
    monthly_expenses: float = 150000
    current_equity: float = 5000000
    current_debt: float = 2000000
    current_epf: float = 1500000
    current_ppf: float = 500000
    current_nps: float = 0
    current_gold: float = 500000
    current_real_estate_equity: float = 0
    current_cash: float = 500000
    monthly_equity_sip: float = 50000
    monthly_debt_sip: float = 20000
    monthly_epf_employee: float = 15000
    monthly_ppf: float = 12500
    monthly_nps: float = 0
    monthly_gold: float = 5000
    equity_return: float = 13.0   # % — will be divided by 100
    debt_return: float = 7.5
    inflation: float = 6.5
    target_monthly_expense_today: float = 150000
    healthcare_buffer_monthly: float = 20000
    travel_annual: float = 500000
    swr: float = 3.5              # % — will be divided by 100
    fire_type: str = "fat"
    annual_savings_growth: float = 8.0


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    insights = get_community_insights()
    benchmarks = get_fire_benchmarks()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "insights": insights,
        "benchmarks": benchmarks,
        "scrape_status": _scrape_status,
    })


@app.post("/calculate")
async def calculate(req: CalculatorRequest):
    inp = FIREInputs(
        current_age=req.current_age,
        target_retire_age=req.target_retire_age,
        life_expectancy=req.life_expectancy,
        monthly_income=req.monthly_income,
        monthly_expenses=req.monthly_expenses,
        current_equity=req.current_equity,
        current_debt=req.current_debt,
        current_epf=req.current_epf,
        current_ppf=req.current_ppf,
        current_nps=req.current_nps,
        current_gold=req.current_gold,
        current_real_estate_equity=req.current_real_estate_equity,
        current_cash=req.current_cash,
        monthly_equity_sip=req.monthly_equity_sip,
        monthly_debt_sip=req.monthly_debt_sip,
        monthly_epf_employee=req.monthly_epf_employee,
        monthly_ppf=req.monthly_ppf,
        monthly_nps=req.monthly_nps,
        monthly_gold=req.monthly_gold,
        equity_return=req.equity_return / 100,
        debt_return=req.debt_return / 100,
        inflation=req.inflation / 100,
        target_monthly_expense_today=req.target_monthly_expense_today,
        healthcare_buffer_monthly=req.healthcare_buffer_monthly,
        travel_annual=req.travel_annual,
        swr=req.swr / 100,
        fire_type=req.fire_type,
        annual_savings_growth=req.annual_savings_growth / 100,
    )
    result = calculate_fire(inp)

    def to_inr(v):
        return format_inr(v) if v else "₹0"

    total_monthly_inv = (
        req.monthly_equity_sip + req.monthly_debt_sip
        + req.monthly_epf_employee * 2 + req.monthly_ppf
        + req.monthly_nps + req.monthly_gold
    )
    savings_rate = ((req.monthly_income - req.monthly_expenses) / req.monthly_income * 100) if req.monthly_income else 0

    return JSONResponse({
        "on_track": result.on_track,
        "fire_corpus_required": result.fire_corpus_required,
        "fire_corpus_required_fmt": to_inr(result.fire_corpus_required),
        "corpus_with_healthcare": to_inr(result.corpus_with_healthcare),
        "projected_corpus_at_target": result.projected_corpus_at_target,
        "projected_corpus_fmt": to_inr(result.projected_corpus_at_target),
        "corpus_gap": result.corpus_gap,
        "corpus_gap_fmt": to_inr(abs(result.corpus_gap)),
        "years_to_fire": result.years_to_fire,
        "projected_fire_age": result.projected_fire_age,
        "inflation_adjusted_monthly_expense": to_inr(result.inflation_adjusted_monthly_expense),
        "annual_withdrawal": to_inr(result.annual_withdrawal),
        "monthly_withdrawal": to_inr(result.monthly_withdrawal),
        "corpus_lasts_years": result.corpus_lasts_years,
        "corpus_survives": result.corpus_survives_to_life_expectancy,
        "estimated_annual_tax": to_inr(result.estimated_annual_tax),
        "effective_tax_rate": result.effective_tax_rate,
        "coast_fire_corpus": to_inr(result.coast_fire_corpus),
        "coast_fire_achievable": result.coast_fire_achievable,
        "half_fire_age": result.half_fire_age,
        "monthly_savings_needed": to_inr(result.monthly_savings_needed),
        "recommendations": result.recommendations,
        "savings_rate": round(savings_rate, 1),
        "total_monthly_investment": to_inr(total_monthly_inv),
        "corpus_breakdown": {
            "Equity MF": result.equity_corpus,
            "Debt": result.debt_corpus,
            "EPF": result.epf_corpus,
            "PPF": result.ppf_corpus,
            "NPS": result.nps_corpus,
            "Gold": result.gold_corpus,
        },
        "corpus_breakdown_fmt": {
            "Equity MF": to_inr(result.equity_corpus),
            "Debt": to_inr(result.debt_corpus),
            "EPF": to_inr(result.epf_corpus),
            "PPF": to_inr(result.ppf_corpus),
            "NPS": to_inr(result.nps_corpus),
            "Gold": to_inr(result.gold_corpus),
        },
    })


@app.post("/analyze")
async def analyze_stream(req: CalculatorRequest):
    """Stream Claude AI analysis grounded in r/fatfireindia community data."""
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not has_api_key:
        async def no_key():
            msg = "Set ANTHROPIC_API_KEY to unlock AI-powered personalized analysis."
            yield f"data: {json.dumps({'text': msg})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(no_key(), media_type="text/event-stream")

    # Calculate FIRE results first (reuse calculate logic)
    inp = FIREInputs(
        current_age=req.current_age,
        target_retire_age=req.target_retire_age,
        life_expectancy=req.life_expectancy,
        monthly_income=req.monthly_income,
        monthly_expenses=req.monthly_expenses,
        current_equity=req.current_equity,
        current_debt=req.current_debt,
        current_epf=req.current_epf,
        current_ppf=req.current_ppf,
        current_nps=req.current_nps,
        current_gold=req.current_gold,
        current_real_estate_equity=req.current_real_estate_equity,
        current_cash=req.current_cash,
        monthly_equity_sip=req.monthly_equity_sip,
        monthly_debt_sip=req.monthly_debt_sip,
        monthly_epf_employee=req.monthly_epf_employee,
        monthly_ppf=req.monthly_ppf,
        monthly_nps=req.monthly_nps,
        monthly_gold=req.monthly_gold,
        equity_return=req.equity_return / 100,
        debt_return=req.debt_return / 100,
        inflation=req.inflation / 100,
        target_monthly_expense_today=req.target_monthly_expense_today,
        healthcare_buffer_monthly=req.healthcare_buffer_monthly,
        travel_annual=req.travel_annual,
        swr=req.swr / 100,
        fire_type=req.fire_type,
        annual_savings_growth=req.annual_savings_growth / 100,
    )
    result   = calculate_fire(inp)
    def to_inr(v): return format_inr(v) if v else "₹0"

    fire_results = {
        "on_track":                result.on_track,
        "fire_corpus_required_fmt": to_inr(result.fire_corpus_required),
        "projected_corpus_fmt":    to_inr(result.projected_corpus_at_target),
        "corpus_gap_fmt":          to_inr(abs(result.corpus_gap)),
        "projected_fire_age":      result.projected_fire_age,
        "monthly_withdrawal":      to_inr(result.monthly_withdrawal),
        "corpus_survives":         result.corpus_survives_to_life_expectancy,
        "corpus_lasts_years":      result.corpus_lasts_years,
        "coast_fire_achievable":   result.coast_fire_achievable,
        "coast_fire_corpus":       to_inr(result.coast_fire_corpus),
        "savings_rate":            round((req.monthly_income - req.monthly_expenses) / req.monthly_income * 100) if req.monthly_income else 0,
    }

    user_data = req.model_dump()

    from intelligence import async_stream_analysis

    async def generate():
        try:
            async for chunk in async_stream_analysis(user_data, fire_results):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks):
    if _scrape_status["running"]:
        return {"status": "already_running"}
    background_tasks.add_task(_run_scrape)
    return {"status": "started"}


async def _run_scrape():
    global _scrape_status
    _scrape_status.update({"running": True, "done": False, "stage": "starting", "error": None})

    def progress_cb(info: dict):
        _scrape_status["stage"] = info.get("stage", "")
        if "total" in info:
            stage = info["stage"]
            if "comment" in stage:
                _scrape_status["comment_count"] = info["total"]
            else:
                _scrape_status["post_count"] = info["total"]

    try:
        loop = asyncio.get_event_loop()
        from scraper import scrape as do_scrape
        stats = await loop.run_in_executor(None, lambda: do_scrape(progress_cb=progress_cb))
        insights = get_community_insights()
        _scrape_status.update({
            "running": False,
            "done": True,
            "stage": "complete",
            "post_count": insights.get("post_count", 0),
            "comment_count": insights.get("comment_count", 0),
            "error": None,
        })
    except Exception as e:
        _scrape_status.update({"running": False, "stage": "error", "error": str(e)})


@app.get("/scrape/status")
async def scrape_status():
    insights = get_community_insights()
    return {**_scrape_status, **insights}


@app.get("/community-insights")
async def community_insights():
    insights = get_community_insights()
    benchmarks = get_fire_benchmarks()
    return {"insights": insights, "benchmarks": benchmarks}


@app.get("/health")
async def health():
    return {"status": "ok", "db_exists": DB_PATH.exists()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
