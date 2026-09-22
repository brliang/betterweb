"""Worker CLI: ``python -m app.worker cycle run``.

The nightly crawl cycle (PLAN.md §6.1) is wired up in milestone M10; stages land in M3-M6.
"""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    commands = parser.add_subparsers(dest="command", required=True)
    cycle = commands.add_parser("cycle", help="crawl cycle commands")
    cycle_commands = cycle.add_subparsers(dest="cycle_command", required=True)
    cycle_commands.add_parser("run", help="run all cycle stages")

    parser.parse_args(argv)
    print("cycle run: not implemented yet (milestone M10)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
