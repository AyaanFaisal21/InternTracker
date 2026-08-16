"""CLI entry point.

  intake run                one detection/verification cycle
  intake run --no-verify    detect + rule-gate only; no API key needed
  intake loop -i 120        continuous, one cycle every 120 s
  intake serve -p 8000      localhost dashboard over the current db
"""

from __future__ import annotations

import argparse
import logging
import time

from .config import load_settings
from .pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="intake")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run one cycle")
    run.add_argument("--no-verify", action="store_true",
                     help="stop after the rule gate; postings park at GATED")

    loop = sub.add_parser("loop", help="run continuously")
    loop.add_argument("-i", "--interval", type=int, default=120, help="seconds between cycles")
    loop.add_argument("--no-verify", action="store_true")

    serve_p = sub.add_parser("serve", help="serve the localhost dashboard")
    serve_p.add_argument("-p", "--port", type=int, default=8000)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()

    if args.cmd == "serve":
        from .web import serve
        serve(settings.db_path, args.port)
        return

    pipeline = Pipeline(settings)
    verify = not args.no_verify
    if args.cmd == "run":
        _report(pipeline.run_cycle(verify=verify))
    else:
        while True:
            _report(pipeline.run_cycle(verify=verify))
            time.sleep(args.interval)


def _report(r) -> None:
    logging.info(
        "cycle: detected=%d new=%d rule_rejected=%d merged=%d collapsed=%d "
        "agent_rejected=%d verified=%d published=%d errors=%d",
        r.detected, r.new, r.rule_rejected, r.merged, r.collapsed,
        r.agent_rejected, r.verified, r.published, len(r.errors),
    )
    for e in r.errors:
        logging.warning("cycle error: %s", e)


if __name__ == "__main__":
    main()
