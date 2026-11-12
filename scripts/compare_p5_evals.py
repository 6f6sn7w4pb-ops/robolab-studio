"""Compare finished P5 evaluation runs on the episodes they share.

  compare_p5_evals.py --output-dir DIR RUN [RUN ...]

RUN is an evaluation directory (or its summary.json), optionally prefixed with a label:
  teacher=outputs/p5_eval_teacher  mock=outputs/p5_eval_mock
Writes comparison.json, comparison.md, episodes.csv and figures/*.png (needs matplotlib).
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', type=Path, required=True, help='new directory for the comparison')
    parser.add_argument('runs', nargs='+', help='[label=]evaluation directory or summary.json')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from robolab.p5_compare import load_run, parse_run_argument, write_comparison

    try:
        runs = [load_run(path, label) for label, path in map(parse_run_argument, args.runs)]
        result = write_comparison(runs, args.output_dir.resolve())
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"shared episodes: {result['shared_episodes']}")
    for warning in result['warnings']:
        print('warning:', warning)
    for run in result['runs']:
        overall = run['shared']['overall']
        print(f"{run['label']:>24}: success {overall['success']['count']}/{overall['episodes']}, "
              f"part hit {overall['part_hit']['count']}, collision {overall['collision']['count']}")
    print('written:', args.output_dir/'comparison.md')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
