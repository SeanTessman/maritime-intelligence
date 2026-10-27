"""Build and execute notebooks/01_data_exploration.ipynb from code (reproducible)."""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf
from nbconvert.preprocessors import ExecutePreprocessor

ROOT = Path(__file__).resolve().parents[1]
cells = [
    nbf.v4.new_markdown_cell(
        "# 01 - Data exploration (NOAA AIS, NY Harbor, 9-15 Jan 2023)\n\n"
        "Reads the cleaned observations and derived features produced by the pipeline "
        "(`python tasks.py pipeline`). Synthetic demo vessels are excluded.\n\n"
        "> This is an educational maritime analytics portfolio project. Anomaly detections represent "
        "statistical or rule-based deviations, not evidence of illegal or hostile activity."
    ),
    nbf.v4.new_code_cell(
        "import sys, json\nsys.path.insert(0, '../src')\nimport numpy as np, pandas as pd\n"
        "import matplotlib.pyplot as plt\nfrom maritime.database import read_sql\n"
        "from maritime.config import get_settings\nplt.rcParams.update({'figure.figsize': (9, 3.6), "
        "'axes.spines.top': False, 'axes.spines.right': False})\nBLUE, ORANGE = '#2a78d6', '#eb6834'"
    ),
    nbf.v4.new_markdown_cell("## Ingestion and data quality"),
    nbf.v4.new_code_cell(
        'runs = read_sql("SELECT file_name, rows_raw, rows_in_bbox, rows_accepted, rows_rejected, rows_duplicate, "\n'
        '                "rows_inserted FROM ingestion_runs ORDER BY file_name")\n'
        "runs['pct_in_bbox'] = (100 * runs.rows_in_bbox / runs.rows_raw).round(2)\nruns"
    ),
    nbf.v4.new_code_cell(
        "qr = read_sql(\"SELECT quality_report FROM ingestion_runs WHERE status='completed'\").quality_report\n"
        "miss = pd.DataFrame([q['missing_by_field'] for q in qr]).sum()\n"
        "(100 * miss / runs.rows_in_bbox.sum()).sort_values(ascending=False).round(2).rename('% missing').to_frame()"
    ),
    nbf.v4.new_markdown_cell("## Reported speed by vessel-type group"),
    nbf.v4.new_code_cell(
        "steps = pd.read_parquet(get_settings().processed_dir / 'steps.parquet',\n"
        "                        columns=['mmsi', 'base_datetime', 'sog', 'dt_s', 'implied_kn', 'vessel_type', 'is_synthetic'])\n"
        "steps = steps[~steps.is_synthetic]\nfrom maritime.processing.vessel_features import vessel_type_group\n"
        "steps['group'] = steps.vessel_type.map(vessel_type_group)\n"
        "moving = steps[steps.sog > 0.5]\nmoving.groupby('group').sog.describe(percentiles=[.5, .95]).round(1)"
    ),
    nbf.v4.new_code_cell(
        "fig, ax = plt.subplots()\nax.hist(steps.sog.dropna().clip(0, 40), bins=80, color=BLUE)\n"
        "ax.set_yscale('log'); ax.set_xlabel('reported SOG (kn, clipped at 40)'); ax.set_ylabel('fixes (log)')\n"
        "ax.set_title('Most fixes are stationary or slow; the long right tail is ferries and a few errors');"
    ),
    nbf.v4.new_markdown_cell(
        "## Reporting intervals and gaps\n\nNOAA's files are downsampled to roughly one "
        "fix per minute, so the interval distribution peaks near 60 s."
    ),
    nbf.v4.new_code_cell(
        "dt = steps.dt_s.dropna()\nprint(dt.describe(percentiles=[.05, .5, .95, .99]).round(1))\n"
        "fig, ax = plt.subplots()\nax.hist(np.log10(dt.clip(1, 86400)), bins=80, color=BLUE)\n"
        "ax.set_xlabel('log10 seconds between consecutive fixes'); ax.set_ylabel('count')\n"
        "ax.axvline(np.log10(1800), color=ORANGE, lw=2); ax.text(np.log10(1800)+.05, ax.get_ylim()[1]*.8, '30 min segment break');"
    ),
    nbf.v4.new_markdown_cell(
        "## Implied vs reported speed\n\nLarge disagreements at short intervals are mostly "
        "GNSS noise; the speed-discrepancy rule only looks at 30-600 s intervals and >200 m steps."
    ),
    nbf.v4.new_code_cell(
        "s = steps[(steps.dt_s.between(30, 600)) & steps.sog.notna()].sample(50000, random_state=0)\n"
        "fig, ax = plt.subplots(figsize=(5, 5))\nax.scatter(s.sog, s.implied_kn.clip(0, 60), s=2, alpha=.15, color=BLUE)\n"
        "ax.plot([0, 40], [0, 40], color=ORANGE, lw=1.5); ax.set_xlim(0, 40); ax.set_ylim(0, 60)\n"
        "ax.set_xlabel('reported SOG (kn)'); ax.set_ylabel('implied speed (kn)');"
    ),
    nbf.v4.new_markdown_cell("## Track features and model scores (aggregate only)"),
    nbf.v4.new_code_cell(
        "tf = pd.read_parquet(get_settings().processed_dir / 'track_features.parquet')\ntf = tf[~tf.is_synthetic]\n"
        "sc = pd.read_parquet(get_settings().processed_dir / 'track_scores.parquet')\n"
        "print(len(tf), 'vessel-day tracks;', sc.split.value_counts().to_dict())\n"
        "fig, ax = plt.subplots()\nfor split, c in [('train (in-sample)', BLUE), ('evaluation (out-of-sample)', ORANGE)]:\n"
        "    ax.hist(sc[sc.split == split].score, bins=50, alpha=.7, color=c, label=split, density=True)\n"
        "thr = json.load(open('../reports/model_cards/real_data_diagnostics.json'))['threshold']\n"
        "ax.axvline(thr, color='k', lw=1); ax.legend(); ax.set_xlabel('Isolation Forest score (higher = more unusual)');"
    ),
    nbf.v4.new_code_cell(
        "json.load(open('../reports/model_cards/real_data_diagnostics.json'))['seed_stability']"
    ),
    nbf.v4.new_markdown_cell(
        "## Takeaways\n\n- ~3% of nationwide fixes fall in the NY Harbor box; hard rejects "
        "there are essentially zero because NOAA pre-cleans, so the soft flags (heading 511, "
        "COG 360) carry most quality information.\n- Tugs and ferries dominate the harbour; "
        "vessel-type grouping matters for any speed rule.\n- Isolation Forest scores are "
        "stable across seeds and similar between training and later days, which supports "
        "using them as a ranking for review, not as a classifier."
    ),
]
nb = nbf.v4.new_notebook(
    cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3"}}
)
ExecutePreprocessor(timeout=600, kernel_name="python3").preprocess(
    nb, {"metadata": {"path": str(ROOT / "notebooks")}}
)
out = ROOT / "notebooks" / "01_data_exploration.ipynb"
nbf.write(nb, out)
print("wrote", out)
