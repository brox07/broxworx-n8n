import argparse
import logging

from . import db
from .config import load_config
from .ingest import make_client, run_ingest
from .scoring import make_scorer, run_scoring


def main() -> None:
    parser = argparse.ArgumentParser(prog="research_digest")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the database if needed and apply the schema")
    sub.add_parser("ingest", help="fetch all sources once and store new items")
    score = sub.add_parser("score", help="score new items for relevance with Clef")
    score.add_argument("--limit", type=int, help="score at most this many items (handy for a first test)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()

    if args.command == "init-db":
        db.ensure_database(cfg.database_url)
        with db.connect(cfg.database_url) as conn:
            db.migrate(conn)
    elif args.command == "ingest":
        with db.connect(cfg.database_url) as conn, make_client() as client:
            db.migrate(conn)
            results = run_ingest(cfg, conn, client)
        for name, count in results.items():
            print(f"{name:24} {'error' if count < 0 else f'{count} new'}")
    elif args.command == "score":
        with db.connect(cfg.database_url) as conn, make_client() as client:
            db.migrate(conn)
            counts = run_scoring(cfg, conn, make_scorer(cfg, client), limit=args.limit)
        print(", ".join(f"{k}: {v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
