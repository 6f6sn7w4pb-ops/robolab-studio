"""Side-by-side comparison of P5 evaluation runs (pure file reading; no simulator).

Inputs are finished P5 run directories (or their summary.json). Every run is re-aggregated on
the episodes all runs share, so rates are compared on identical scenes and instructions, and
each pair of runs gets an exact McNemar test on the paired per-episode outcomes. Outputs:

  comparison.json   schema robolab.p5_comparison.v1 (all numbers below, plus run provenance)
  comparison.md     tables for a report (Chinese headings, metric keys in English)
  episodes.csv      one row per shared episode, one column per run and metric
  figures/*.png     success rate by split and the four headline rates, with 95 % Wilson CIs
"""
import csv
import io
import json
import math
from pathlib import Path

from .p5_eval import SUMMARY_SCHEMA, group_stats

COMPARISON_SCHEMA = 'robolab.p5_comparison.v1'
METRICS = ('success', 'part_hit', 'lifted', 'collision')
PAIRED = ('success', 'part_hit', 'collision')
LABELS_ZH = dict(success='成功率', part_hit='部位命中率', lifted='抬起率', collision='碰撞率',
                 wrong_part_contacted='错部位接触率', part_accuracy_given_contact='接触时部位正确率')
SPLITS_ZH = dict(test_instance='已见形状族新实例', test_unseen='未见形状族')
PHRASING_ZH = dict(seen='已见说法', held_out='未见说法')
# Reference categorical order (validated light-mode palette); identity follows the run's position.
SERIES = ('#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948')
MAX_RUNS = len(SERIES)


def load_run(path, label=None):
    path = Path(path)
    summary_path = path/'summary.json' if path.is_dir() else path
    summary = json.loads(summary_path.read_bytes())
    if summary.get('schema') != SUMMARY_SCHEMA:
        raise ValueError(f'{summary_path}: not a P5 evaluation summary')
    return dict(label=label or summary_path.parent.name, path=str(summary_path.parent.resolve()), summary=summary)


def parse_run_argument(text):
    """'label=path' or 'path'."""
    label, separator, path = text.partition('=')
    return (label, path) if separator and label and not Path(text).exists() else (None, text)


def mcnemar_exact(only_first, only_second):
    """Two-sided exact McNemar p-value (binomial test on the discordant pairs)."""
    discordant = only_first+only_second
    if discordant == 0:
        return 1.
    tail = sum(math.comb(discordant, k) for k in range(min(only_first, only_second)+1))/2**discordant
    return min(1., 2*tail)


def _rows(run, case_ids):
    by_case = {item['case_id']: item for item in run['summary']['runs']}
    return [by_case[case_id] for case_id in case_ids]


def _groups(rows):
    def stats(selected):
        return group_stats([dict(metrics=row['metrics'], error=row.get('error')) for row in selected])

    groups = {'overall': stats(rows)}
    for key, field in (('split', lambda row: row['split']), ('split_phrasing', lambda row: row['split']+'/'+row['phrasing']),
                       ('family', lambda row: row['family']), ('part', lambda row: row['part'])):
        names = sorted({field(row) for row in rows})
        groups['by_'+key] = {name: stats([row for row in rows if field(row) == name]) for name in names}
    return groups


def paired(first_rows, second_rows, metric):
    both = only_first = only_second = neither = 0
    for a, b in zip(first_rows, second_rows):
        x, y = bool(a['metrics'][metric]), bool(b['metrics'][metric])
        both += x and y
        only_first += x and not y
        only_second += y and not x
        neither += not x and not y
    total = both+only_first+only_second+neither
    return dict(metric=metric, episodes=total, both=both, only_first=only_first, only_second=only_second,
                neither=neither, rate_difference=(only_first-only_second)/total if total else None,
                mcnemar_exact_p=mcnemar_exact(only_first, only_second))


def conditions_text(summary):
    conditions = summary.get('conditions') or {}
    parts = [conditions.get('scene_set', 'dataset')]
    if conditions.get('anti_roll'):
        parts.append('anti-roll')
    appearance = conditions.get('appearance') or []
    parts.append('appearance:'+'+'.join(appearance) if appearance else 'nominal look')
    return ', '.join(parts)


def compare(runs):
    if not 2 <= len(runs) <= MAX_RUNS:
        raise ValueError(f'compare 2-{MAX_RUNS} runs')
    if len({run['label'] for run in runs}) != len(runs):
        raise ValueError('run labels must be unique')
    evaluated = [{item['case_id'] for item in run['summary']['runs'] if item['split'] != 'control'} for run in runs]
    order = [item['case_id'] for item in runs[0]['summary']['runs']]
    common = [case_id for case_id in order if all(case_id in ids for ids in evaluated)]
    if not common:
        raise ValueError('the runs share no evaluated episode')
    warnings = []
    for key in ('plan_sha256', 'horizon', 'language', 'execute_steps'):
        values = {json.dumps(run['summary'].get(key)) for run in runs}
        if len(values) > 1:
            warnings.append(f'{key} differs between runs: '+', '.join(sorted(values)))
    for run, ids in zip(runs, evaluated):
        summary = run['summary']
        if summary['status'] != 'finalized':
            warnings.append(f"{run['label']}: status {summary['status']} ({summary['finished']}/{summary['planned']})")
        if len(ids) > len(common):
            warnings.append(f"{run['label']}: {len(ids)-len(common)} evaluated episodes are outside the shared set")
    rows = {run['label']: _rows(run, common) for run in runs}
    meta = rows[runs[0]['label']]
    for run in runs[1:]:
        for a, b in zip(meta, rows[run['label']]):
            if (a['split'], a['part'], a['instruction']) != (b['split'], b['part'], b['instruction']):
                raise ValueError(f"episode {a['case_id']} differs in split, part or instruction between runs")
    pairs = []
    for i, first in enumerate(runs):
        for second in runs[i+1:]:
            pairs.append(dict(first=first['label'], second=second['label'],
                              tests=[paired(rows[first['label']], rows[second['label']], metric) for metric in PAIRED]))
    return dict(
        schema=COMPARISON_SCHEMA, shared_episodes=len(common), shared_case_ids=common, warnings=warnings,
        runs=[dict(label=run['label'], path=run['path'], policy=run['summary']['policy'],
                   is_vla=run['summary'].get('is_vla'), conditions=run['summary'].get('conditions'),
                   conditions_text=conditions_text(run['summary']), status=run['summary']['status'],
                   finished=run['summary']['finished'], planned=run['summary']['planned'],
                   harness_checks=run['summary'].get('harness_checks'), own_results=run['summary'].get('results'),
                   shared=_groups(rows[run['label']])) for run in runs],
        pairs=pairs)


# ----------------------------------------------------------------------------- writers

def pct(stat, of=None):
    if not stat or stat.get('rate') is None:
        return '—'
    low, high = stat['ci95']
    total = stat.get('of', of)
    return f"{stat['count']}/{total} · {100*stat['rate']:.1f}% [{100*low:.1f}–{100*high:.1f}]"


def markdown(result):
    lines = ['# P5 评估对比', '',
             f"共享条目：{result['shared_episodes']} 条（所有运行都评估过的非对照条目）。下表比例都在这同一批条目上重算，"
             '方括号内为 95% Wilson 区间。', '']
    if result['warnings']:
        lines += ['**注意**', ''] + [f'- {text}' for text in result['warnings']] + ['']
    lines += ['## 运行', '', '| 标签 | 策略 | 真实 VLA | 场景条件 | 完成 | 复跑一致 | 与数据集逐帧一致 | 与数据集物理一致 |', '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for run in result['runs']:
        checks = run['harness_checks'] or {}
        flag = {True: '是', False: '否', None: '—'}
        lines.append(f"| {run['label']} | `{run['policy']['policy_id']}` | {flag[run['is_vla']]} | {run['conditions_text']} | "
                     f"{run['finished']}/{run['planned']} | {flag[checks.get('recheck_identical')]} | "
                     f"{flag[checks.get('reference_identical')]} | {flag[checks.get('reference_physics_identical')]} |")
    lines += ['', '“物理一致”只比较动作、qpos 和判定，不比较图像；外观变化下应为“是”而逐帧一致为“否”。'
              '开了防滚的圆柱条目按设计会偏离示范，不计入这两项检查。', '']
    header = '| 标签 | ' + ' | '.join(LABELS_ZH[m] for m in METRICS) + ' | 接触时部位正确率 |'
    rule = '| --- |' + ' --- |'*(len(METRICS)+1)

    def table(title, select):
        out = [f'## {title}', '', header, rule]
        for run in result['runs']:
            group = select(run['shared'])
            if group is None:
                continue
            out.append(f"| {run['label']} | " + ' | '.join(pct(group[m], group['episodes']) for m in METRICS)
                       + f" | {pct(group['part_accuracy_given_contact'])} |")
        return out + ['']

    lines += table('全部共享条目', lambda shared: shared['overall'])
    splits = sorted({name for run in result['runs'] for name in run['shared']['by_split']})
    for split in splits:
        lines += table(SPLITS_ZH.get(split, split), lambda shared, split=split: shared['by_split'].get(split))
        for phrasing in ('seen', 'held_out'):
            key = f'{split}/{phrasing}'
            lines += table(f"{SPLITS_ZH.get(split, split)} · {PHRASING_ZH[phrasing]}",
                           lambda shared, key=key: shared['by_split_phrasing'].get(key))
    lines += ['## 成对比较（McNemar 精确检验）', '',
              '同一条目上两次运行的结果配对：“仅 A”是该指标在 A 上成立、在 B 上不成立的条数。差值 = (仅 A − 仅 B) / 条数。'
              'p 值为双侧；多组比较时请自行做多重比较校正。', '',
              '| A | B | 指标 | 条数 | 都成立 | 仅 A | 仅 B | 都不成立 | 差值 | p |', '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for pair in result['pairs']:
        for test in pair['tests']:
            lines.append(f"| {pair['first']} | {pair['second']} | {LABELS_ZH[test['metric']]} | {test['episodes']} | "
                         f"{test['both']} | {test['only_first']} | {test['only_second']} | {test['neither']} | "
                         f"{100*test['rate_difference']:+.1f}% | {test['mcnemar_exact_p']:.3g} |")
    if result.get('figures'):
        lines += ['', '## 图', ''] + [line for name in result['figures'] for line in (f'![{name}]({name})', '')]
    lines += ['', '## 未通过原因（共享条目）', '']
    for run in result['runs']:
        reasons = run['shared']['overall']['failure_reasons']
        text = '、'.join(f'`{name}` {count}' for name, count in sorted(reasons.items(), key=lambda item: -item[1])) or '无'
        lines.append(f"- **{run['label']}**：{text}")
    return '\n'.join(lines)+'\n'


def episodes_csv(result, runs):
    rows = {run['label']: {item['case_id']: item for item in run['summary']['runs']} for run in runs}
    first = rows[runs[0]['label']]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(['case_id', 'split', 'family', 'part', 'phrasing']
                    + [f'{run["label"]}:{metric}' for run in runs for metric in METRICS])
    for case_id in result['shared_case_ids']:
        meta = first[case_id]
        writer.writerow([case_id, meta['split'], meta['family'], meta['part'], meta['phrasing']]
                        + [int(bool(rows[run['label']][case_id]['metrics'][metric])) for run in runs for metric in METRICS])
    return buffer.getvalue()


def figures(result, directory):
    """Grouped bars with 95 % Wilson whiskers; one colour per run in fixed order."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.size': 9, 'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e',
                         'xtick.color': '#52514e', 'ytick.color': '#52514e', 'text.color': '#0b0b0b'})
    runs = result['runs']
    written = []

    def grouped(categories, select, title, filename):
        fig, ax = plt.subplots(figsize=(1.2+1.15*len(categories)*max(1, len(runs)/2), 3.4), dpi=200)
        width = .8/len(runs)
        for index, run in enumerate(runs):
            stats = [select(run, key) for key, _ in categories]
            xs = [c+(index-(len(runs)-1)/2)*width for c in range(len(categories))]
            values = [100*(s['rate'] or 0.) if s else 0. for s in stats]
            low = [max(0., v-100*s['ci95'][0]) if s and s['rate'] is not None else 0. for v, s in zip(values, stats)]
            high = [max(0., 100*s['ci95'][1]-v) if s and s['rate'] is not None else 0. for v, s in zip(values, stats)]
            ax.bar(xs, values, width*.92, color=SERIES[index], label=run['label'], zorder=2)
            ax.errorbar(xs, values, yerr=[low, high], fmt='none', ecolor='#52514e', elinewidth=1, capsize=2, zorder=3)
            # A run-coloured tick on the baseline keeps a 0 % bar identifiable; the label gives the value.
            ax.hlines([0.]*len(xs), [x-width*.46 for x in xs], [x+width*.46 for x in xs], colors=SERIES[index],
                      linewidth=2.5, zorder=4)
            for x, value, upper, stat in zip(xs, values, high, stats):
                if stat and stat['rate'] is not None:
                    ax.text(x, value+upper+1.5, f'{value:.0f}', ha='center', va='bottom', fontsize=6.5,
                            color='#52514e')
        ax.set_xticks(range(len(categories)))
        ax.set_xticklabels([label for _, label in categories])
        ax.set_ylim(0, 112)
        ax.set_yticks(range(0, 101, 20))
        ax.set_ylabel('rate (%) · 95% Wilson CI')
        ax.set_title(title, loc='left', fontsize=10)
        ax.grid(axis='y', color='#e4e3df', linewidth=.6, zorder=0)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        ax.legend(frameon=False, fontsize=8, loc='upper left', bbox_to_anchor=(1., 1.))
        fig.tight_layout()
        fig.savefig(directory/filename, facecolor='#fcfcfb')
        plt.close(fig)
        written.append(f'figures/{filename}')

    metric_names = dict(success='success', part_hit='part hit', lifted='lifted', collision='collision')
    grouped([(m, metric_names[m]) for m in METRICS], lambda run, m: run['shared']['overall'][m],
            f"Headline rates on {result['shared_episodes']} shared episodes", 'rates_overall.png')
    splits = sorted({name for run in runs for name in run['shared']['by_split_phrasing']},
                    key=lambda name: (name.split('/')[0], name.split('/')[1] != 'seen'))
    split_names = dict(test_instance='seen family', test_unseen='unseen family')
    categories = [(name, f"{split_names.get(name.split('/')[0], name.split('/')[0])}\n{name.split('/')[1].replace('_', '-')} prompt")
                  for name in splits]
    grouped(categories, lambda run, name: (run['shared']['by_split_phrasing'].get(name) or {}).get('success'),
            'Success rate by split and prompt phrasing', 'success_by_split.png')
    return written


def write_comparison(runs, output):
    output.mkdir(parents=True, exist_ok=False)
    result = compare(runs)
    result['figures'] = figures(result, output/'figures')
    (output/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    (output/'comparison.md').write_text(markdown(result), encoding='utf-8')
    (output/'episodes.csv').write_text(episodes_csv(result, runs), encoding='utf-8')
    return result
