#!/usr/bin/env python3
"""`python -m trip_dumps` - trip-dump pipeline entry point.

Routes to the same subcommands as the main CLI's `trip-dump` step:

    python -m trip_dumps cluster --source ~/photos --out-json trips.json
    python -m trip_dumps curate --source ~/photos --out-dir ./review
    python -m trip_dumps --help
"""
import sys

from .cluster_trips import main as cluster_main
from .curate_carousel import main as curate_main

STEPS = {"cluster": cluster_main, "curate": curate_main}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m trip_dumps {cluster|curate} [script args...]\n")
        print("  cluster  cluster photos into trips "
              "(trip_dumps/cluster_trips.py; append --help for its flags)")
        print("  curate   curate a carousel from a trip "
              "(trip_dumps/curate_carousel.py; append --help for its flags)")
        return 0
    step, rest = argv[0], argv[1:]
    if step not in STEPS:
        print(f"unknown step {step!r}; expected one of: "
              f"{', '.join(sorted(STEPS))}", file=sys.stderr)
        return 2
    return STEPS[step](rest)


if __name__ == "__main__":
    sys.exit(main())
