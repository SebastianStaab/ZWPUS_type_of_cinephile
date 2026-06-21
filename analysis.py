"""
BLOCKBUSTER QUALITY ANALYSIS 1960-2025
=======================================

WHAT THIS SCRIPT DOES
----------------------
1. Data exploration  – basic overview of the dataset (coverage, missing values, outliers)
2. Q1: Quality over time  – how do all ratings evolve year by year?
3. Q2: Are blockbusters getting worse?  – statistical trend test
4. Q3/Q4: Nostalgia bias  – do people rate films from their childhood higher?

DATA SOURCES
------------
- Box office rankings: top-10 domestic US grossers per year (1960-2025),
  compiled from training knowledge, verified against The-Numbers.com for
  selected years. Ranking errors are possible for pre-1980 decades.
- IMDB, Letterboxd avg, Tomatometer: from training knowledge.
  RT is very sparse for pre-1990 films.
  Metascore is kept in the CSV but NOT used in analysis (only 0.6% coverage).
- Personal ratings:
    Seb    (born 1996) – exported from IMDB (1-10 scale),   98 / 660 films
    David  (born 1981) – scraped from letterboxd.com/behaind (0.5-5 scale), 405 / 660
    Robert (born 1987) – scraped from letterboxd.com/robsntown (own, preferred)
                         + letterboxd.com/roberthofmannio (older fan-curated), 228 / 660

MISSING VALUES – HOW THEY ARE HANDLED
--------------------------------------
- Non-numeric strings (e.g. corrupted values) → converted to NaN (not a number).
- Zeros → treated as NaN (no rating system uses 0 as a valid score).
- Correlations: only film pairs where BOTH ratings exist are used (pairwise complete cases).
- Annual averages: computed from whatever films have data that year (NaN rows skipped).
- Nostalgia OLS: only rows with all required columns present are used.
- Result: each analysis states how many films (n=...) it actually used.

SCALE NORMALISATION  →  all scores converted to 0–1 so they're comparable
---------------------------------------------------------------------------
  IMDB           ÷ 10    (original: 1–10)
  Letterboxd avg ÷ 5     (original: 0.5–5)
  Tomatometer    ÷ 100   (original: 0–100 %)
  Seb's score    ÷ 10    (IMDB scale)
  David's score  ÷ 5     (Letterboxd scale)
  Robert's score ÷ 5     (Letterboxd scale)

KNOWN BIASES & CAVEATS
-----------------------
- Selection bias in personal ratings: David and Robert haven't rated every film.
  Films they DID rate from older decades tend to be the most prominent ones,
  likely making older films look better in their data.
- Letterboxd skews toward cinephiles; IMDB skews toward general audience.
  Neither is a perfectly neutral "objective" measure.
- "Formative era" = first 20 years of life. This definition is arbitrary.
  Results might differ with 15 or 25 years.
- RT coverage is very sparse before 1990, so the composite quality score is
  dominated by IMDB + Letterboxd for older films.
- Seb has only seen 98/660 films (~15%), making his nostalgia analysis
  underpowered (small sample).
"""

import warnings; warnings.filterwarnings('ignore')
import os, math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# ── paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH   = os.path.join(SCRIPT_DIR, 'blockbusters_1960_2025.csv')
ALPHA = 0.05   # significance threshold (industry standard: 5 %)

# ── people ────────────────────────────────────────────────────────────────────
BIRTH = {'seb_n': 1996, 'david_n': 1981, 'robert_n': 1987}
NAMES = {'seb_n': 'Seb (born 1996)', 'david_n': 'David (born 1981)',
         'robert_n': 'Robert (born 1987)'}

# ── colours ───────────────────────────────────────────────────────────────────
COLOR = {
    'imdb_n':   '#e07b54',   # orange
    'lb_n':     '#5ba4cf',   # blue
    'rt_n':     '#c585c0',   # purple
    'seb_n':    '#f5c842',   # yellow
    'david_n':  '#e84545',   # red
    'robert_n': '#3d7ebf',   # dark blue
}


# =============================================================================
# MINIMAL STATS HELPERS  (no scipy needed)
# =============================================================================

def _norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def _t_p(t_stat, df):
    """Two-sided p-value for a t-statistic."""
    # Normal approximation is accurate enough for df > 30
    if df > 30:
        return 2 * (1 - _norm_cdf(abs(t_stat)))
    # Exact-ish: regularised incomplete beta series
    x = df / (df + t_stat ** 2)
    a, b = df / 2, 0.5
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    try:
        term = math.exp(a * math.log(x) + b * math.log(1 - x) - lbeta) / a
        ibeta = term
        for k in range(1, 200):
            term *= (a + b + k - 1) * x / (a + k)
            ibeta += term
            if abs(term) < 1e-12:
                break
        return 2 * min(ibeta, 1 - ibeta)
    except (ValueError, OverflowError):
        return 0.0

def pearsonr(x, y):
    """Correlation coefficient r and two-sided p-value."""
    n = len(x)
    if n < 3:
        return np.nan, np.nan
    xm, ym = x - x.mean(), y - y.mean()
    r = float((xm * ym).sum() / (math.sqrt((xm**2).sum() * (ym**2).sum()) + 1e-300))
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        return r, 0.0
    t = r * math.sqrt((n - 2) / (1 - r ** 2))
    return r, _t_p(t, n - 2)

def welch_t(a, b):
    """Welch's t-test (works with unequal sample sizes and variances)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return np.nan, np.nan
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return 0.0, 1.0
    t = (a.mean() - b.mean()) / se
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return t, _t_p(t, df)

def cohen_d(a, b):
    """Effect size: how big is the difference in standard-deviation units?
    Rule of thumb: 0.2 = small, 0.5 = medium, 0.8 = large."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    pooled = math.sqrt(((len(a)-1)*a.var(ddof=1) + (len(b)-1)*b.var(ddof=1)) / (len(a)+len(b)-2))
    return (a.mean() - b.mean()) / pooled if pooled > 0 else 0.0

def simple_linregress(x, y):
    """Ordinary least-squares linear regression. Returns slope, intercept, r, p."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    xm, ym = x.mean(), y.mean()
    ssxx = ((x - xm) ** 2).sum()
    ssxy = ((x - xm) * (y - ym)).sum()
    slope = ssxy / ssxx
    intercept = ym - slope * xm
    y_hat = intercept + slope * x
    ss_res = ((y - y_hat) ** 2).sum()
    ss_tot = ((y - ym) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    r  = math.copysign(math.sqrt(abs(r2)), slope)
    se = math.sqrt(ss_res / max(len(x) - 2, 1) / ssxx) if ssxx > 0 else np.nan
    t  = slope / se if se and se > 0 else np.nan
    p  = _t_p(t, len(x) - 2) if not (t is None or np.isnan(t)) else np.nan
    return slope, intercept, r, p

def ols_residuals(X, y):
    """Multi-variable OLS via numpy. Returns predicted values and residuals.
    Residual = actual score − what objective metrics would predict.
    Positive residual = rated higher than 'deserved'; negative = rated lower."""
    X_aug = np.column_stack([X, np.ones(len(X))])
    coef, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)
    pred  = X_aug @ coef
    return pred, y - pred

def stars(p):
    """Significance stars for quick visual reading."""
    return '***' if p < 0.001 else ('**' if p < 0.01 else ('*' if p < 0.05 else 'n.s.'))


# =============================================================================
# 0.  LOAD & CLEAN
# =============================================================================
df = pd.read_csv(CSV_PATH)

# Convert all score columns to numbers; anything non-numeric becomes NaN
SCORE_COLS = ['imdb_rating', 'letterboxd_rating', 'tomatometer',
              'sebs_score', 'davids_score_letterboxd', 'roberts_score_letterboxd']
for c in SCORE_COLS:
    df[c] = pd.to_numeric(df[c], errors='coerce')
    df[c] = df[c].where(df[c] > 0)   # zero is not a valid rating → NaN

# Normalise all scores to [0, 1] for comparability
df['imdb_n']   = df['imdb_rating']             / 10.0
df['lb_n']     = df['letterboxd_rating']       / 5.0
df['rt_n']     = df['tomatometer']             / 100.0
df['seb_n']    = df['sebs_score']              / 10.0
df['david_n']  = df['davids_score_letterboxd'] / 5.0
df['robert_n'] = df['roberts_score_letterboxd']/ 5.0

# Clamp anything outside [0,1] (data errors) → NaN
NORM_COLS   = ['imdb_n', 'lb_n', 'rt_n', 'seb_n', 'david_n', 'robert_n']
NORM_LABELS = ['IMDB',   'LB avg', 'Tomatometer', 'Seb', 'David', 'Robert']
for c in NORM_COLS:
    df[c] = df[c].where((df[c] >= 0) & (df[c] <= 1))

OBJ_COLS  = ['imdb_n', 'lb_n', 'rt_n']   # "objective" metrics (no metascore – <1% coverage)
PERS_COLS = ['seb_n', 'david_n', 'robert_n']

df['decade'] = (df['year'] // 10) * 10


# =============================================================================
# SECTION 0 – DATA EXPLORATION
# =============================================================================
print("=" * 65)
print("DATA EXPLORATION")
print("=" * 65)
print(f"\nTotal films: {len(df)}  |  Years: {df['year'].min()}–{df['year'].max()}")
print(f"Films per year: {df.groupby('year').size().unique()} (should all be 10)")

print("\n── Coverage (how many films have each rating) ──────────────")
for c, lbl in zip(NORM_COLS, NORM_LABELS):
    n = df[c].notna().sum()
    bar = '█' * int(n / len(df) * 30)
    print(f"  {lbl:14s} {bar:<30} {n:4d}/{len(df)} ({n/len(df)*100:.1f}%)")

print("\n── Descriptive statistics (normalised 0–1 scale) ───────────")
desc = df[NORM_COLS].describe().loc[['mean', 'std', 'min', 'max']]
desc.columns = NORM_LABELS
print(desc.round(3).to_string())

print("\n── Sample of films with most complete data ─────────────────")
df['n_ratings'] = df[NORM_COLS].notna().sum(axis=1)
sample = df.nlargest(10, 'n_ratings')[['year', 'title', 'n_ratings'] + NORM_COLS]
print(sample.round(3).to_string(index=False))

print("\n── Potentially bad values removed ──────────────────────────")
raw_bad = {
    'imdb_rating': df[(df['imdb_rating'].notna()) & ((df['imdb_rating'] < 1) | (df['imdb_rating'] > 10))],
    'letterboxd_rating': df[(df['letterboxd_rating'].notna()) & ((df['letterboxd_rating'] < 0.5) | (df['letterboxd_rating'] > 5))],
    'tomatometer': df[(df['tomatometer'].notna()) & ((df['tomatometer'] < 0) | (df['tomatometer'] > 100))],
}
any_bad = False
for col, bad_df in raw_bad.items():
    if len(bad_df):
        any_bad = True
        for _, row in bad_df.iterrows():
            print(f"  {row['title']} ({row['year']}): {col} = {row[col]}  → removed")
if not any_bad:
    print("  None found after cleaning.")


# =============================================================================
# FIGURE 1 – QUALITY OVER TIME
# =============================================================================
# Annual mean per metric (NaN rows are skipped automatically by groupby.mean).
# 3-year rolling average to smooth noise while keeping responsiveness.
# Two panels: (1) objective/critic metrics  (2) personal ratings.
# Order of personal ratings: David, Robert, Seb (Seb last).

yr = df.groupby('year')[NORM_COLS].mean()

OBJ_PLOT  = ['imdb_n', 'lb_n', 'rt_n']
OBJ_LBL   = ['IMDB', 'Letterboxd avg', 'Tomatometer']
PERS_PLOT = ['david_n', 'robert_n', 'seb_n']
PERS_LBL  = ['David', 'Robert', 'Seb']

fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
fig.suptitle('How have blockbuster ratings changed over time? (1960–2025)\n'
             '3-year rolling average | Shaded = formative eras (red=David, blue=Robert, green=Seb)',
             fontsize=12)

shade_kw = dict(alpha=0.09)
for ax in axes:
    ax.axvspan(1981, 2001, color='red',   **shade_kw)
    ax.axvspan(1987, 2007, color='blue',  **shade_kw)
    ax.axvspan(1996, 2016, color='green', **shade_kw)

for col, lbl in zip(OBJ_PLOT, OBJ_LBL):
    roll = yr[col].rolling(3, center=True, min_periods=2).mean().dropna()
    axes[0].plot(roll.index, roll.values, color=COLOR[col], lw=2.2, label=lbl)

for col, lbl in zip(PERS_PLOT, PERS_LBL):
    roll = yr[col].rolling(3, center=True, min_periods=2).mean().dropna()
    axes[1].plot(roll.index, roll.values, color=COLOR[col], lw=2.2, label=lbl)

axes[0].set_title('Critic / aggregate ratings  (IMDB · Letterboxd · Tomatometer)')
axes[1].set_title('Personal ratings  (David · Robert · Seb)')
for ax in axes:
    ax.set_ylabel('Rating (0 = worst, 1 = best)')
    ax.set_ylim(0.2, 1.0)
    ax.grid(axis='y', alpha=0.3)
    ax.legend(loc='upper right', fontsize=9, ncol=3)

axes[1].set_xlabel('Year')
plt.tight_layout()
fig.savefig(os.path.join(SCRIPT_DIR, 'fig1_quality_over_time.png'), dpi=150)
plt.close(fig)
print("\nSaved fig1_quality_over_time.png")


# =============================================================================
# FIGURE 2 – CORRELATION MATRIX
# =============================================================================
# For each pair of metrics, we only use films where BOTH values exist (pairwise complete cases).
# n= in each cell tells you how many films that correlation is based on.

n = len(NORM_COLS)
corr_mat = np.full((n, n), np.nan)
corr_p   = np.full((n, n), np.nan)
corr_n   = np.zeros((n, n), dtype=int)

for i, c1 in enumerate(NORM_COLS):
    for j, c2 in enumerate(NORM_COLS):
        both = df[[c1, c2]].dropna()
        corr_n[i, j] = len(both)
        if len(both) >= 10:
            r, p = pearsonr(both[c1].values, both[c2].values)
            corr_mat[i, j] = r
            corr_p[i, j]   = p

fig, ax = plt.subplots(figsize=(8, 7))
im = ax.imshow(corr_mat, vmin=-1, vmax=1, cmap='RdYlGn')
plt.colorbar(im, ax=ax, label='Pearson r  (−1 = opposite, 0 = no link, +1 = identical)')
ax.set_xticks(range(n)); ax.set_xticklabels(NORM_LABELS, rotation=30, ha='right')
ax.set_yticks(range(n)); ax.set_yticklabels(NORM_LABELS)
for i in range(n):
    for j in range(n):
        v = corr_mat[i, j]
        if not np.isnan(v):
            txt = f'{v:.2f}{stars(corr_p[i,j])}\nn={corr_n[i,j]}'
            ax.text(j, i, txt, ha='center', va='center', fontsize=7.5,
                    color='black' if abs(v) < 0.6 else 'white')
ax.set_title('Do the different rating systems agree with each other?\n'
             '(Each cell = films where BOTH ratings exist)\n'
             'Stars: * p<.05  ** p<.01  *** p<.001  n.s. = not significant')
plt.tight_layout()
fig.savefig(os.path.join(SCRIPT_DIR, 'fig2_correlation_matrix.png'), dpi=150)
plt.close(fig)
print("Saved fig2_correlation_matrix.png")

print("\n── Pairwise correlations ────────────────────────────────────")
for i, (c1, l1) in enumerate(zip(NORM_COLS, NORM_LABELS)):
    for j, (c2, l2) in enumerate(zip(NORM_COLS[i+1:], NORM_LABELS[i+1:]), i+1):
        both = df[[c1, c2]].dropna()
        if len(both) < 10:
            continue
        r, p = pearsonr(both[c1].values, both[c2].values)
        interp = ('strong' if abs(r) > 0.6 else 'moderate' if abs(r) > 0.3 else 'weak')
        print(f"  {l1:13s} × {l2:13s}  r={r:+.2f} ({interp})  p={p:.4f}{stars(p)}  n={len(both)}")


# =============================================================================
# Q2 – ARE BLOCKBUSTERS GETTING WORSE?
# =============================================================================
# Composite objective quality = mean of IMDB, Letterboxd, RT (whatever is available per film).
# We use the annual mean of that composite, then fit a straight line through time.
# H0 (null hypothesis): no trend over time (slope = 0)
# If the slope is significantly negative → blockbusters are getting worse.

print("\n" + "=" * 65)
print("Q2: ARE BLOCKBUSTERS GETTING WORSE?")
print("=" * 65)

df['obj_quality'] = df[OBJ_COLS].mean(axis=1, skipna=True)
yr_obj = df.groupby('year')['obj_quality'].mean().dropna()
yr_arr = yr_obj.index.values.astype(float)
q_arr  = yr_obj.values

slope, intercept, r, p_ols = simple_linregress(yr_arr, q_arr)
print(f"\n[Linear trend]")
print(f"  Slope: {slope:+.5f} per year  (negative = getting worse)")
print(f"  r = {r:.3f}, r² = {r**2:.3f}  (r² = how much of the variation is explained by time)")
print(f"  p = {p_ols:.4f}  {stars(p_ols)}")
if p_ols < ALPHA:
    direction = 'decline' if slope < 0 else 'improvement'
    print(f"  → H0 REJECTED: there IS a statistically significant {direction} over time.")
else:
    print(f"  → H0 NOT rejected: no clear trend detected.")

# Decade comparison: ANOVA across all decades, then simple mean comparison
df_obj = df.dropna(subset=['obj_quality'])
print(f"\n[Quality by decade – mean ± std]")
decade_groups = {}
for d, grp in df_obj.groupby('decade'):
    vals = grp['obj_quality'].values
    if len(vals) >= 5:
        decade_groups[d] = vals
        print(f"  {d}s: mean={vals.mean():.3f}  std={vals.std():.3f}  n={len(vals)}")

# Simple comparison: every past decade vs 2020s
recent = max(decade_groups)
recent_vals = decade_groups[recent]
print(f"\n[Is each decade better/worse than the 2020s?  (Welch t-test)]")
print(f"  Reference: {recent}s  mean={recent_vals.mean():.3f}")
for d in sorted(decade_groups):
    if d == recent:
        continue
    t, p = welch_t(decade_groups[d], recent_vals)
    diff = decade_groups[d].mean() - recent_vals.mean()
    direction = 'higher' if diff > 0 else 'lower'
    sig = 'significant' if p < ALPHA else 'not significant'
    print(f"  {d}s vs {recent}s:  Δ={diff:+.3f} ({direction})  p={p:.4f}{stars(p)}  ({sig})")

# Figure 3
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

ax1.scatter(yr_arr, q_arr, color='steelblue', s=40, alpha=0.8, zorder=3,
            label='Annual avg quality')
ax1.plot(yr_arr, intercept + slope * yr_arr, 'r-', lw=2.5,
         label=f'Trend: {slope:+.4f}/yr\n(p={p_ols:.4f}  {stars(p_ols)})')
ax1.set_xlabel('Year')
ax1.set_ylabel('Composite quality (0–1)')
ax1.set_title('Are blockbusters getting worse?\n(higher = better)')
ax1.legend(fontsize=9)
ax1.grid(alpha=0.3)

dec_list   = sorted(decade_groups)
dec_vals   = [decade_groups[d] for d in dec_list]
dec_labels = [f"{d}s" for d in dec_list]
bp = ax2.boxplot(dec_vals, labels=dec_labels, patch_artist=True,
                 medianprops=dict(color='red', lw=2))
for patch in bp['boxes']:
    patch.set_facecolor('#cce5ff')
ax2.set_xlabel('Decade')
ax2.set_ylabel('Composite quality (0–1)')
ax2.set_title('Quality by decade\n(box = middle 50 % of films; red line = median)')
ax2.tick_params(axis='x', rotation=30)
ax2.grid(axis='y', alpha=0.3)

plt.tight_layout()
fig.savefig(os.path.join(SCRIPT_DIR, 'fig3_trend_and_decades.png'), dpi=150)
plt.close(fig)
print("\nSaved fig3_trend_and_decades.png")


# =============================================================================
# Q3 & Q4 – NOSTALGIA BIAS
# =============================================================================
# Approach:
# Step 1 (raw): Do people rate formative-era films higher? Simple comparison.
# Step 2 (residual): Control for actual film quality.
#   We predict each person's score from objective metrics (IMDB, LB, RT) using OLS.
#   Residual = how much higher/lower they rated a film than objective metrics predict.
#   If formative-era residuals are systematically higher → nostalgia premium
#   even after accounting for the films' actual quality.
#
# H0: formative and non-formative residuals are equal (no nostalgia bias)
# H0 rejected if p < 0.05

print("\n" + "=" * 65)
print("Q3 & Q4: NOSTALGIA BIAS  (do we rate childhood films too highly?)")
print("=" * 65)
print("Formative era = first 20 years of life.")
print("Residual = personal score MINUS what IMDB/LB/RT would predict.\n"
      "  Positive residual → rated higher than 'deserved'\n"
      "  Negative residual → rated lower than 'deserved'\n")

fig_raw, axes_raw = plt.subplots(1, 3, figsize=(15, 5))
fig_res, axes_res = plt.subplots(1, 3, figsize=(15, 5))
fig_raw.suptitle('Nostalgia bias – raw ratings: formative vs. non-formative era', fontsize=12)
fig_res.suptitle('Nostalgia bias – residuals: do they rate childhood films ABOVE what critics predict?',
                 fontsize=11)

for idx, pers in enumerate(PERS_COLS):
    birth = BIRTH[pers]
    name  = NAMES[pers]
    form_end = birth + 20

    sub = df[df[pers].notna()].copy()
    sub['formative'] = sub['year'].between(birth, form_end - 1)

    form_vals    = sub.loc[sub['formative'],  pers].values
    nonform_vals = sub.loc[~sub['formative'], pers].values

    print(f"── {name} ({'Formative: ' + str(birth) + '–' + str(form_end-1)}) ──")
    print(f"  Films rated (total): {len(sub)}")
    print(f"  Formative era:       {len(form_vals)} films  avg={form_vals.mean():.3f}" if len(form_vals) else "  Formative era: 0 films")
    print(f"  Non-formative:       {len(nonform_vals)} films  avg={nonform_vals.mean():.3f}" if len(nonform_vals) else "  Non-formative: 0 films")

    ax_r = axes_raw[idx]
    ax_s = axes_res[idx]

    if len(form_vals) < 5 or len(nonform_vals) < 5:
        print("  → Too few films to test.\n")
        for ax in [ax_r, ax_s]:
            ax.text(0.5, 0.5, f'{name}\nToo few data points',
                    ha='center', va='center', transform=ax.transAxes)
        continue

    t_raw, p_raw = welch_t(form_vals, nonform_vals)
    d_raw = cohen_d(form_vals, nonform_vals)
    direction = 'higher' if form_vals.mean() > nonform_vals.mean() else 'lower'
    print(f"\n  STEP 1 – RAW comparison:")
    print(f"    Formative films rated {direction} (Δ={form_vals.mean()-nonform_vals.mean():+.3f})")
    print(f"    t-test: p={p_raw:.4f} {stars(p_raw)}  effect size d={d_raw:.2f}")
    print(f"    → H0 {'REJECTED' if p_raw < ALPHA else 'NOT rejected'}")

    # Step 2: residual analysis
    sub2 = sub[OBJ_COLS + [pers, 'formative', 'year', 'title']].dropna(subset=OBJ_COLS + [pers])
    if len(sub2) < 15:
        print("  → Not enough complete rows for residual analysis.\n")
        # still draw the raw plot
        bp = ax_r.boxplot([form_vals, nonform_vals],
                          labels=[f'Formative\n{birth}–{form_end-1}', 'Non-\nformative'],
                          patch_artist=True, medianprops=dict(color='red', lw=2))
        bp['boxes'][0].set_facecolor('#ffcccb'); bp['boxes'][1].set_facecolor('#cce5ff')
        ax_r.set_title(f'{name}\np={p_raw:.4f} {stars(p_raw)}  d={d_raw:.2f}', fontsize=10)
        ax_r.set_ylabel('Personal rating (0–1)'); ax_r.grid(axis='y', alpha=0.3)
        ax_s.text(0.5, 0.5, 'Not enough complete data', ha='center', va='center',
                  transform=ax_s.transAxes)
        print()
        continue

    pred, resid = ols_residuals(sub2[OBJ_COLS].values, sub2[pers].values)
    sub2 = sub2.copy()
    sub2['predicted'] = pred
    sub2['residual']  = resid

    form_res    = sub2.loc[sub2['formative'],  'residual'].values
    nonform_res = sub2.loc[~sub2['formative'], 'residual'].values

    t_res, p_res = welch_t(form_res, nonform_res)
    d_res = cohen_d(form_res, nonform_res)
    print(f"\n  STEP 2 – RESIDUAL analysis (controlling for actual film quality):")
    print(f"    Formative residual:     {form_res.mean():+.3f}  (rates childhood films THIS much above prediction)")
    print(f"    Non-formative residual: {nonform_res.mean():+.3f}")
    print(f"    t-test: p={p_res:.4f} {stars(p_res)}  effect size d={d_res:.2f}")
    print(f"    → H0 {'REJECTED – nostalgia bias confirmed!' if p_res < ALPHA else 'NOT rejected – no clear bias after controlling for quality.'}")
    print()

    # Plot raw
    bp = ax_r.boxplot([form_vals, nonform_vals],
                      labels=[f'Formative\n{birth}–{form_end-1}', 'Non-\nformative'],
                      patch_artist=True, medianprops=dict(color='red', lw=2))
    bp['boxes'][0].set_facecolor('#ffcccb'); bp['boxes'][1].set_facecolor('#cce5ff')
    ax_r.set_title(f'{name}\np={p_raw:.4f} {stars(p_raw)}  d={d_raw:.2f}', fontsize=10)
    ax_r.set_ylabel('Personal rating (0–1)')
    ax_r.text(0.5, 0.02, f'n_form={len(form_vals)}  n_non={len(nonform_vals)}',
              transform=ax_r.transAxes, ha='center', fontsize=8)
    ax_r.grid(axis='y', alpha=0.3)

    # Plot residuals: scatter of actual vs predicted, coloured by era
    colors_era = sub2['formative'].map({True: '#e84545', False: '#3d7ebf'})
    ax_s.scatter(sub2['predicted'], sub2[pers], c=colors_era, alpha=0.5, s=20)
    lo = min(sub2['predicted'].min(), sub2[pers].min()) - 0.02
    hi = max(sub2['predicted'].max(), sub2[pers].max()) + 0.02
    ax_s.plot([lo, hi], [lo, hi], 'k--', lw=1, label='No bias (diagonal)')
    handles = [
        Line2D([0],[0], marker='o', color='w', markerfacecolor='#e84545', ms=8,
               label=f'Formative (avg residual {form_res.mean():+.2f})'),
        Line2D([0],[0], marker='o', color='w', markerfacecolor='#3d7ebf', ms=8,
               label=f'Non-formative (avg {nonform_res.mean():+.2f})'),
        Line2D([0],[0], color='k', linestyle='--', label='No bias'),
    ]
    ax_s.legend(handles=handles, fontsize=7)
    ax_s.set_xlabel('Predicted score (from IMDB, LB, RT)')
    ax_s.set_ylabel('Actual personal rating')
    ax_s.set_title(f'{name}\nresidual p={p_res:.4f} {stars(p_res)}  d={d_res:.2f}', fontsize=10)
    ax_s.grid(alpha=0.3)

fig_raw.tight_layout()
fig_res.tight_layout()
fig_raw.savefig(os.path.join(SCRIPT_DIR, 'fig4a_nostalgia_raw.png'), dpi=150)
fig_res.savefig(os.path.join(SCRIPT_DIR, 'fig4b_nostalgia_residual.png'), dpi=150)
plt.close('all')
print("Saved fig4a_nostalgia_raw.png  fig4b_nostalgia_residual.png")


# =============================================================================
# FIGURE 5 – RATING PREMIUM BY DECADE  (residual bar chart)
# =============================================================================
fig, axes = plt.subplots(1, 3, figsize=(15, 5))
fig.suptitle('By how much does each person rate each decade above/below\n'
             'what IMDB + Letterboxd + RT would predict?\n'
             '(Red bars = formative era; error bars = 95 % confidence interval)',
             fontsize=11)

for idx, pers in enumerate(PERS_COLS):
    birth    = BIRTH[pers]
    name     = NAMES[pers]
    form_end = birth + 20
    ax = axes[idx]

    sub2 = df[df[pers].notna()][OBJ_COLS + [pers, 'decade']].dropna(subset=OBJ_COLS + [pers])
    if len(sub2) < 15:
        ax.text(0.5, 0.5, 'Insufficient data', ha='center', va='center',
                transform=ax.transAxes)
        continue

    pred, resid = ols_residuals(sub2[OBJ_COLS].values, sub2[pers].values)
    sub2 = sub2.copy(); sub2['residual'] = resid

    dec_res = sub2.groupby('decade')['residual'].agg(['mean', 'sem', 'count'])
    dec_res = dec_res[dec_res['count'] >= 3]

    bar_colors = ['#e84545' if (birth <= d < form_end) else '#aaaaaa'
                  for d in dec_res.index]
    ax.bar([f"{int(d)}s" for d in dec_res.index], dec_res['mean'],
           color=bar_colors, yerr=dec_res['sem'] * 1.96, capsize=4, alpha=0.85)
    ax.axhline(0, color='black', lw=1)
    ax.set_title(f'{name}\nFormative: {birth}--{form_end-1} (red)', fontsize=10)
    ax.set_xlabel('Decade')
    ax.set_ylabel('Avg residual  (+= rates higher than predicted)')
    ax.tick_params(axis='x', rotation=30)
    ax.grid(axis='y', alpha=0.3)
    for i, (d, row) in enumerate(dec_res.iterrows()):
        ax.text(i, row['mean'] + row['sem'] * 1.96 + 0.005,
                f"n={int(row['count'])}", ha='center', fontsize=7)

plt.tight_layout()
fig.savefig(os.path.join(SCRIPT_DIR, 'fig5_decade_premium.png'), dpi=150)
plt.close(fig)
print("Saved fig5_decade_premium.png")

print("\nAll figures saved to:", SCRIPT_DIR)
