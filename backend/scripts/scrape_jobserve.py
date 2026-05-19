import argparse
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.scrapers.job_boards import ScraperOrchestrator
from app.scrapers.jobserve import JobServeAdapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape JobServe via the hidden jobIDs field and direct detail AJAX endpoint.")
    parser.add_argument("search_url", help="JobServe search URL, for example https://www.jobserve.com/gb/en/JobSearch.aspx?...")
    parser.add_argument("--max-pages", type=int, default=1, help="Maximum search result pages to inspect.")
    parser.add_argument("--max-jobs", type=int, default=None, help="Maximum job detail records to fetch.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent detail fetch workers.")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum delay between HTTP requests across workers.")
    parser.add_argument("--json-out", default="jobserve_jobs.json", help="JSON output file.")
    parser.add_argument("--csv-out", default="jobserve_jobs.csv", help="CSV output file.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    adapter = JobServeAdapter(min_delay_seconds=args.delay)
    orchestrator = ScraperOrchestrator(adapter)
    records = orchestrator.run_and_export(
        args.search_url,
        json_path=Path(args.json_out),
        csv_path=Path(args.csv_out),
        max_pages=args.max_pages,
        max_jobs=args.max_jobs,
        max_workers=args.workers,
    )
    logging.getLogger(__name__).info("exported %s jobs json=%s csv=%s", len(records), args.json_out, args.csv_out)


if __name__ == "__main__":
    main()
