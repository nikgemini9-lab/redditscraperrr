"""
India FIRE Calculator Engine
Powered by community wisdom from r/fatfireindia + sound financial math.

Key India-specific assumptions (community-validated):
- Inflation: 6-7% (India CPI historically)
- Equity returns: 12-14% nominal (Nifty 50 long-term ~13%)
- Debt returns: 7-8% (G-Secs, FD, corporate bonds)
- Gold returns: 8% nominal
- Real estate rental yield: 2-3% + 5% appreciation
- Safe Withdrawal Rate: 3.0-3.5% (lower than 4% rule due to higher inflation)
- EPF interest: 8.15% (current rate)
- PPF interest: 7.1%
- NPS equity: 10-12%
"""
from dataclasses import dataclass, field
from typing import Optional
import math


# ── India tax slabs (FY 2024-25, New Regime) ──────────────────────────────────
NEW_REGIME_SLABS = [
    (300000, 0.00),
    (600000, 0.05),
    (900000, 0.10),
    (1200000, 0.15),
    (1500000, 0.20),
    (float("inf"), 0.30),
]
SURCHARGE_BRACKETS = [
    (5000000, 0.10),
    (10000000, 0.15),
    (20000000, 0.25),
    (50000000, 0.37),
]
HEALTH_EDUCATION_CESS = 0.04
LTCG_EQUITY_RATE = 0.125   # 12.5% above 1.25L
STCG_EQUITY_RATE = 0.20
LTCG_DEBT_RATE = 0.30      # taxed at slab rate as of 2024


def calc_income_tax_new_regime(income: float) -> float:
    """Calculate income tax under new regime."""
    if income <= 700000:  # rebate u/s 87A
        return 0.0
    tax = 0.0
    prev = 0
    for limit, rate in NEW_REGIME_SLABS:
        if income <= prev:
            break
        taxable = min(income, limit) - prev
        tax += taxable * rate
        prev = limit
    # Surcharge
    surcharge = 0.0
    for threshold, rate in SURCHARGE_BRACKETS:
        if income > threshold:
            surcharge = tax * rate
    tax += surcharge
    tax *= (1 + HEALTH_EDUCATION_CESS)
    return round(tax, 2)


def calc_sip_corpus(monthly_sip: float, annual_return: float, years: int) -> float:
    """Future value of SIP."""
    r = annual_return / 12
    n = years * 12
    if r == 0:
        return monthly_sip * n
    return monthly_sip * ((((1 + r) ** n) - 1) / r) * (1 + r)


def calc_lumpsum_growth(amount: float, annual_return: float, years: int) -> float:
    """Future value of lump sum."""
    return amount * ((1 + annual_return) ** years)


def inflation_adjusted(amount: float, inflation: float, years: int) -> float:
    """Real value of future amount."""
    return amount / ((1 + inflation) ** years)


def years_to_fire(
    current_corpus: float,
    monthly_savings: float,
    target_corpus: float,
    annual_return: float,
    annual_savings_growth: float = 0.08,
) -> Optional[int]:
    """Binary search for years until FIRE corpus is reached."""
    corpus = current_corpus
    monthly_savings_cur = monthly_savings
    for year in range(1, 61):
        # Grow existing corpus
        corpus = corpus * (1 + annual_return)
        # Add SIP contributions (savings grow YoY with salary hikes)
        corpus += calc_sip_corpus(monthly_savings_cur, annual_return, 1)
        monthly_savings_cur *= (1 + annual_savings_growth)
        if corpus >= target_corpus:
            return year
    return None


@dataclass
class FIREInputs:
    # Personal
    current_age: int = 32
    target_retire_age: int = 45
    life_expectancy: int = 85
    spouse_age: Optional[int] = None

    # Income & Savings
    monthly_income: float = 300000       # Net take-home
    monthly_expenses: float = 150000
    monthly_savings: float = 0           # Will be auto-calculated if 0
    annual_savings_growth: float = 0.08  # Salary hike rate

    # Current Assets
    current_equity: float = 5000000
    current_debt: float = 2000000
    current_epf: float = 1500000
    current_ppf: float = 500000
    current_nps: float = 0
    current_real_estate_equity: float = 0
    current_gold: float = 0
    current_cash: float = 500000

    # Monthly Contributions
    monthly_equity_sip: float = 50000
    monthly_debt_sip: float = 20000
    monthly_epf_employee: float = 15000
    monthly_ppf: float = 12500          # Max 1.5L/year
    monthly_nps: float = 0
    monthly_gold: float = 5000

    # Return Assumptions (nominal, annualized)
    equity_return: float = 0.13
    debt_return: float = 0.075
    epf_return: float = 0.0815
    ppf_return: float = 0.071
    nps_return: float = 0.11
    gold_return: float = 0.08
    real_estate_appreciation: float = 0.06
    inflation: float = 0.065

    # FIRE Targets
    target_monthly_expense_today: float = 150000  # In today's Rs
    healthcare_buffer_monthly: float = 20000
    travel_annual: float = 500000
    emergency_fund_months: int = 12

    # SWR
    swr: float = 0.035   # 3.5% — community consensus for India

    # Type
    fire_type: str = "fat"   # lean / regular / fat / coast / barista


@dataclass
class FIREResults:
    # Targets
    inflation_adjusted_monthly_expense: float = 0
    fire_corpus_required: float = 0
    corpus_with_healthcare: float = 0

    # Current Trajectory
    years_to_fire: Optional[int] = None
    projected_fire_age: Optional[int] = None
    projected_corpus_at_target: float = 0
    corpus_gap: float = 0
    on_track: bool = False

    # Corpus Breakdown at Target
    equity_corpus: float = 0
    debt_corpus: float = 0
    epf_corpus: float = 0
    ppf_corpus: float = 0
    nps_corpus: float = 0
    gold_corpus: float = 0
    total_corpus: float = 0

    # Post-FIRE
    annual_withdrawal: float = 0
    monthly_withdrawal: float = 0
    corpus_lasts_years: Optional[float] = None
    corpus_survives_to_life_expectancy: bool = False

    # Tax
    estimated_annual_tax: float = 0
    effective_tax_rate: float = 0

    # Milestones
    half_fire_age: Optional[int] = None
    coast_fire_corpus: float = 0
    coast_fire_achievable: bool = False

    # Recommendations
    recommendations: list = field(default_factory=list)
    monthly_savings_needed: float = 0


def calculate_fire(inp: FIREInputs) -> FIREResults:
    r = FIREResults()
    years_working = inp.target_retire_age - inp.current_age
    if years_working < 0:
        years_working = 0

    # ── Monthly savings ──────────────────────────────────────────────────────
    if inp.monthly_savings == 0:
        inp.monthly_savings = inp.monthly_income - inp.monthly_expenses

    # ── Inflation-adjusted expense at retirement ─────────────────────────────
    base_expense = inp.target_monthly_expense_today + inp.healthcare_buffer_monthly
    r.inflation_adjusted_monthly_expense = round(
        base_expense * ((1 + inp.inflation) ** years_working), 2
    )

    # ── FIRE corpus required ─────────────────────────────────────────────────
    annual_expense_at_retire = r.inflation_adjusted_monthly_expense * 12
    # Add travel
    annual_expense_at_retire += inp.travel_annual * ((1 + inp.inflation) ** years_working)

    r.fire_corpus_required = round(annual_expense_at_retire / inp.swr, 2)

    # Adjust for fat FIRE types
    fire_multiplier = {"lean": 0.7, "regular": 1.0, "fat": 1.4, "coast": 1.0, "barista": 0.8}
    r.fire_corpus_required = round(r.fire_corpus_required * fire_multiplier.get(inp.fire_type, 1.0), 2)

    r.corpus_with_healthcare = round(r.fire_corpus_required * 1.1, 2)  # 10% health buffer

    # ── Project corpus at target retirement age ──────────────────────────────
    y = years_working

    r.equity_corpus = round(
        calc_lumpsum_growth(inp.current_equity, inp.equity_return, y)
        + calc_sip_corpus(inp.monthly_equity_sip + inp.monthly_epf_employee * 0, inp.equity_return, y),
        2,
    )
    # Use separate SIPs
    equity_sip_corpus = calc_sip_corpus(inp.monthly_equity_sip, inp.equity_return, y)
    r.equity_corpus = round(
        calc_lumpsum_growth(inp.current_equity, inp.equity_return, y) + equity_sip_corpus, 2
    )

    r.debt_corpus = round(
        calc_lumpsum_growth(inp.current_debt, inp.debt_return, y)
        + calc_sip_corpus(inp.monthly_debt_sip, inp.debt_return, y), 2
    )

    r.epf_corpus = round(
        calc_lumpsum_growth(inp.current_epf, inp.epf_return, y)
        + calc_sip_corpus(inp.monthly_epf_employee * 2, inp.epf_return, y),  # employee + employer
        2,
    )

    r.ppf_corpus = round(
        calc_lumpsum_growth(inp.current_ppf, inp.ppf_return, y)
        + calc_sip_corpus(inp.monthly_ppf, inp.ppf_return, y), 2
    )

    r.nps_corpus = round(
        calc_lumpsum_growth(inp.current_nps, inp.nps_return, y)
        + calc_sip_corpus(inp.monthly_nps, inp.nps_return, y), 2
    )

    r.gold_corpus = round(
        calc_lumpsum_growth(inp.current_gold, inp.gold_return, y)
        + calc_sip_corpus(inp.monthly_gold, inp.gold_return, y), 2
    )

    real_estate = round(
        calc_lumpsum_growth(inp.current_real_estate_equity, inp.real_estate_appreciation, y), 2
    )
    cash = round(calc_lumpsum_growth(inp.current_cash, 0.045, y), 2)  # FD returns

    r.total_corpus = round(
        r.equity_corpus + r.debt_corpus + r.epf_corpus + r.ppf_corpus
        + r.nps_corpus + r.gold_corpus + real_estate + cash, 2
    )
    r.projected_corpus_at_target = r.total_corpus

    # ── On track? ────────────────────────────────────────────────────────────
    r.corpus_gap = round(r.fire_corpus_required - r.total_corpus, 2)
    r.on_track = r.total_corpus >= r.fire_corpus_required

    # ── Years to FIRE dynamically ─────────────────────────────────────────────
    current_total_corpus = (
        inp.current_equity + inp.current_debt + inp.current_epf
        + inp.current_ppf + inp.current_nps + inp.current_gold
        + inp.current_real_estate_equity + inp.current_cash
    )
    total_monthly_investment = (
        inp.monthly_equity_sip + inp.monthly_debt_sip
        + inp.monthly_epf_employee * 2 + inp.monthly_ppf
        + inp.monthly_nps + inp.monthly_gold
    )
    blended_return = 0.115  # weighted average
    r.years_to_fire = years_to_fire(
        current_total_corpus,
        total_monthly_investment,
        r.fire_corpus_required,
        blended_return,
        inp.annual_savings_growth,
    )
    r.projected_fire_age = (
        inp.current_age + r.years_to_fire if r.years_to_fire else None
    )

    # ── Post-FIRE withdrawal analysis ────────────────────────────────────────
    r.annual_withdrawal = round(r.fire_corpus_required * inp.swr, 2)
    r.monthly_withdrawal = round(r.annual_withdrawal / 12, 2)

    # Simulate corpus depletion
    post_fire_years = inp.life_expectancy - inp.target_retire_age
    corpus_sim = r.fire_corpus_required
    portfolio_return = blended_return
    for yr in range(1, post_fire_years + 1):
        withdrawal = r.annual_withdrawal * ((1 + inp.inflation) ** yr)
        corpus_sim = corpus_sim * (1 + portfolio_return) - withdrawal
        if corpus_sim <= 0:
            r.corpus_lasts_years = yr - 1
            break
    else:
        r.corpus_lasts_years = post_fire_years
        r.corpus_survives_to_life_expectancy = True

    # ── Tax on withdrawals ────────────────────────────────────────────────────
    annual_income_equiv = r.annual_withdrawal
    r.estimated_annual_tax = calc_income_tax_new_regime(annual_income_equiv)
    r.effective_tax_rate = round(
        (r.estimated_annual_tax / annual_income_equiv * 100) if annual_income_equiv else 0, 1
    )

    # ── Milestones ────────────────────────────────────────────────────────────
    half_target = r.fire_corpus_required / 2
    half_yrs = years_to_fire(current_total_corpus, total_monthly_investment, half_target, blended_return)
    r.half_fire_age = inp.current_age + half_yrs if half_yrs else None

    # Coast FIRE: corpus needed today so it grows to target by retire_age with NO additional saving
    if years_working > 0:
        r.coast_fire_corpus = round(r.fire_corpus_required / ((1 + blended_return) ** years_working), 2)
    r.coast_fire_achievable = current_total_corpus >= r.coast_fire_corpus

    # ── Monthly savings needed to hit target ─────────────────────────────────
    if not r.on_track and years_working > 0:
        gap = r.corpus_gap
        # How much extra monthly SIP needed (blended return over remaining years)
        r_m = blended_return / 12
        n = years_working * 12
        if r_m > 0:
            extra = gap / (((((1 + r_m) ** n) - 1) / r_m) * (1 + r_m))
        else:
            extra = gap / n
        r.monthly_savings_needed = round(total_monthly_investment + max(0, extra), 2)
    else:
        r.monthly_savings_needed = total_monthly_investment

    # ── Recommendations ──────────────────────────────────────────────────────
    recs = []
    savings_rate = (inp.monthly_savings / inp.monthly_income * 100) if inp.monthly_income else 0

    if savings_rate < 40:
        recs.append(f"Your savings rate is {savings_rate:.0f}%. r/fatfireindia community targets 50-70%. Try to cut discretionary expenses.")
    if inp.monthly_equity_sip < inp.monthly_savings * 0.6:
        recs.append("Increase equity allocation. For long horizons (>10 yrs), 70-80% equity is optimal for wealth creation.")
    if inp.current_epf == 0:
        recs.append("Start EPF/VPF contributions. Tax-free 8.15% returns are unbeatable for debt allocation.")
    if inp.current_ppf == 0:
        recs.append("Max out PPF (₹1.5L/year). EEE status makes it India's best tax-free debt instrument.")
    if not r.on_track:
        extra = r.monthly_savings_needed - total_monthly_investment
        recs.append(f"You need ₹{extra:,.0f}/month more to retire at {inp.target_retire_age}. Consider increasing income or delaying retirement by {max(1, (r.projected_fire_age or inp.target_retire_age + 5) - inp.target_retire_age)} years.")
    if r.corpus_lasts_years and r.corpus_lasts_years < post_fire_years:
        recs.append(f"Your corpus may deplete in {r.corpus_lasts_years} years post-retirement. Consider a higher SWR target or part-time income (BaristaFIRE).")
    if inp.fire_type == "fat" and r.fire_corpus_required < 30000000:
        recs.append("For Fat FIRE in India, most community members target ₹3-5 Cr minimum. Consider upgrading target.")
    if inp.current_nps == 0 and inp.monthly_income > 100000:
        recs.append("Consider NPS Tier 1 for additional ₹50K tax deduction u/s 80CCD(1B) under old regime.")
    if r.coast_fire_achievable:
        recs.append(f"Great news! You've already hit Coast FIRE (₹{r.coast_fire_corpus/1e7:.2f} Cr needed). Your corpus will reach target without additional saving.")
    if r.on_track:
        recs.append(f"You're ON TRACK for FIRE at {inp.target_retire_age}! Consider Fat FIRE upgrades or early retirement.")

    r.recommendations = recs
    return r


def format_inr(amount: float) -> str:
    """Format amount in Indian system (Cr/L)."""
    if abs(amount) >= 1e7:
        return f"₹{amount/1e7:.2f} Cr"
    elif abs(amount) >= 1e5:
        return f"₹{amount/1e5:.2f} L"
    else:
        return f"₹{amount:,.0f}"


def get_fire_benchmarks():
    """r/fatfireindia community benchmarks."""
    return {
        "lean_fire_min": 10000000,      # 1 Cr
        "regular_fire_min": 30000000,   # 3 Cr
        "fat_fire_min": 100000000,      # 10 Cr
        "ultra_fat_fire": 300000000,    # 30 Cr
        "typical_metro_monthly_expense": 150000,
        "typical_tier2_monthly_expense": 80000,
        "community_swr": 3.5,
        "community_avg_retire_age": 45,
        "community_corpus_targets": [1, 2, 3, 5, 7, 10, 15, 20, 30, 50],  # in Cr
        "equity_allocation_under_40": 80,   # %
        "equity_allocation_40_50": 70,
        "equity_allocation_above_50": 60,
        "recommended_emergency_fund_months": 12,
    }
