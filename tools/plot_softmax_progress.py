import re
from pathlib import Path
import math
import csv

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

ROOT = Path.cwd()
LOG = ROOT / 'notes' / 'perf_log.md'
OUT_DIR = ROOT / 'results' / 'dense_softmax_progress'
OUT_DIR.mkdir(parents=True, exist_ok=True)

BENCH = 1183
R0_TIME = 10522

text = LOG.read_text(encoding='utf-8')
parts = re.split(r'(?=^## Round \d+\n)', text, flags=re.M)
round_time = {0: R0_TIME}
for part in parts:
    m = re.match(r'^## Round (\d+)', part)
    if not m:
        continue
    r = int(m.group(1))
    vals = []
    for pat in [r'candidate total [`]?([0-9]+)[`]?', r'candidate VF total [`]?([0-9]+)[`]?', r'VF total = [`]?([0-9]+)[`]?']:
        vals.extend(int(x) for x in re.findall(pat, part))
    if vals:
        round_time[r] = vals[-1]

max_round = max(round_time)
rounds = list(range(max_round + 1))
per_round = [round_time.get(r, math.nan) for r in rounds]
best = []
b = math.inf
for r in rounds:
    v = round_time.get(r)
    if v is not None and not math.isnan(v):
        b = min(b, v)
    best.append(b if b < math.inf else math.nan)

perf_round = [BENCH / v * 100 if not math.isnan(v) else math.nan for v in per_round]
perf_best = [BENCH / v * 100 if not math.isnan(v) else math.nan for v in best]

csv_path = OUT_DIR / 'dense_softmax_progress.csv'
with csv_path.open('w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['round', 'round_vf_cycles', 'best_so_far_cycles', 'round_vs_ascendc_percent', 'best_vs_ascendc_percent'])
    for r, v, bv, p, bp in zip(rounds, per_round, best, perf_round, perf_best):
        w.writerow([r, '' if math.isnan(v) else int(v), int(bv), '' if math.isnan(p) else f'{p:.4f}', f'{bp:.4f}'])

annotations = [
    (6, 'batched copy'),
    (22, 'cache exp'),
    (33, 'vector max'),
    (37, 'vector sum'),
    (41, 'reciprocal norm'),
    (54, '2-row fusion'),
    (57, 'shared barrier'),
    (68, '3+3+2 fusion'),
    (74, 'norm order'),
]

plt.rcParams.update({
    'font.size': 11,
    'axes.titlesize': 16,
    'axes.labelsize': 13,
    'legend.fontsize': 11,
    'figure.dpi': 140,
    'savefig.dpi': 220,
    'axes.grid': True,
    'grid.alpha': 0.25,
})

def style_axes(ax):
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_xlim(-1, max_round + 1)
    ax.set_xticks(list(range(0, max_round + 1, 10)))

fig, ax = plt.subplots(figsize=(15, 7.8))
ax.plot(rounds, per_round, linestyle='--', color='#b9b9b9', marker='o', markersize=3.8,
        markerfacecolor='#b9b9b9', markeredgewidth=0, linewidth=1.4, label='Round result')
ax.plot(rounds, best, linestyle='-', color='#1f77b4', marker='o', markersize=4.2,
        markerfacecolor='#1f77b4', markeredgewidth=0, linewidth=2.2, label='Best so far')
ax.axhline(BENCH, color='black', linestyle='--', linewidth=1.4, label='AscendC softmax_v2 1183 cycles')
ax.set_title('Dense Softmax Optimization Progress - VF Time')
ax.set_xlabel('Round')
ax.set_ylabel('VF time (cycles)')
style_axes(ax)
ax.set_ylim(0, max(v for v in per_round if not math.isnan(v)) * 1.08)
for i, (r, label) in enumerate(annotations):
    if r not in round_time:
        continue
    y = best[r]
    dy = [900, 650, 520, 420, 340, 620, 420, 330, 260][i]
    ax.annotate(label, xy=(r, y), xytext=(r + 1.0, y + dy),
                arrowprops=dict(arrowstyle='->', color='#444444', lw=0.8),
                fontsize=9.5, color='#222222',
                bbox=dict(boxstyle='round,pad=0.22', fc='white', ec='#dddddd', alpha=0.9))
ax.legend(loc='upper right', frameon=True)
fig.tight_layout()
time_png = OUT_DIR / 'dense_softmax_vf_time_progress.png'
fig.savefig(time_png)
plt.close(fig)

fig, ax = plt.subplots(figsize=(15, 7.8))
ax.plot(rounds, perf_round, linestyle='--', color='#b9b9b9', marker='o', markersize=3.8,
        markerfacecolor='#b9b9b9', markeredgewidth=0, linewidth=1.4, label='Round result')
ax.plot(rounds, perf_best, linestyle='-', color='#1f77b4', marker='o', markersize=4.2,
        markerfacecolor='#1f77b4', markeredgewidth=0, linewidth=2.2, label='Best so far')
ax.axhline(100, color='black', linestyle='--', linewidth=1.4, label='AscendC softmax_v2 = 100%')
ax.set_title('Dense Softmax Optimization Progress - Relative Performance')
ax.set_xlabel('Round')
ax.set_ylabel('Performance vs AscendC softmax_v2')
style_axes(ax)
ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
ax.set_ylim(0, max(perf_best) * 1.16)
for i, (r, label) in enumerate(annotations):
    if r not in round_time:
        continue
    y = perf_best[r]
    dy = [8, 7, 6, 6, 6, 11, 9, 8, 7][i]
    ax.annotate(label, xy=(r, y), xytext=(r + 1.0, y + dy),
                arrowprops=dict(arrowstyle='->', color='#444444', lw=0.8),
                fontsize=9.5, color='#222222',
                bbox=dict(boxstyle='round,pad=0.22', fc='white', ec='#dddddd', alpha=0.9))
ax.legend(loc='upper left', frameon=True)
fig.tight_layout()
perf_png = OUT_DIR / 'dense_softmax_relative_performance_progress.png'
fig.savefig(perf_png)
plt.close(fig)

print('wrote', time_png)
print('wrote', perf_png)
print('wrote', csv_path)
print('best_cycles', min(v for v in per_round if not math.isnan(v)))
print('best_perf_percent', max(perf_best))
