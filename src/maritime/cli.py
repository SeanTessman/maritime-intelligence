"""Command-line entry point: `python -m maritime.cli <command>`.

`tasks.py` at the repo root wraps these commands for Windows-friendly use.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from maritime.config import PROJECT_ROOT, get_settings
from maritime.utils.logging import setup_logging

logger = logging.getLogger("maritime.cli")


def _dates(args: argparse.Namespace) -> tuple[date, date]:
    s = get_settings()
    start = date.fromisoformat(args.start) if args.start else s.miadp_study_start
    end = date.fromisoformat(args.end) if args.end else s.miadp_study_end
    return start, end


def cmd_download(args: argparse.Namespace) -> None:
    from maritime.ingestion.download_ais import download_range

    start, end = _dates(args)
    for p in download_range(start, end):
        print(p)


def cmd_ingest(args: argparse.Namespace) -> None:
    from maritime.ingestion.download_ais import ais_filename, date_range
    from maritime.ingestion.load_database import ingest_file
    from maritime.sources import NOAA_AIS

    s = get_settings()
    if args.files:
        files = [Path(f) for f in args.files]
    else:
        start, end = _dates(args)
        files = [s.raw_dir / ais_filename(d) for d in date_range(start, end)]
    missing = [f for f in files if not f.exists()]
    if missing:
        sys.exit(f"Missing raw files (run `python tasks.py download`): {[m.name for m in missing]}")
    out_dir = PROJECT_ROOT / "reports" / "data_quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        summary = ingest_file(f, NOAA_AIS, rejected_sample_dir=s.miadp_data_dir / "interim")
        print(
            json.dumps(
                {
                    k: summary.get(k)
                    for k in (
                        "file",
                        "status",
                        "total_raw_observations",
                        "in_study_region",
                        "accepted",
                        "rejected",
                        "inserted",
                        "unique_vessels",
                    )
                },
                default=str,
            )
        )
        if summary["status"] == "completed":
            (out_dir / f"{f.stem}.json").write_text(json.dumps(summary, indent=2, default=str))


def cmd_ports(args: argparse.Namespace) -> None:
    from maritime.ingestion.load_ports import load_wpi

    print(load_wpi(Path(args.file) if args.file else None, buffer_deg=args.buffer))


def cmd_synthetic(args: argparse.Namespace) -> None:
    from maritime.synthetic import load_synthetic_into_db, write_synthetic_files

    paths = write_synthetic_files()
    print({k: str(v) for k, v in paths.items()})
    if args.load:
        print(load_synthetic_into_db())


def cmd_process(args: argparse.Namespace) -> None:
    from maritime.processing.pipeline import run_processing

    print(json.dumps(run_processing(), indent=2, default=str))


def cmd_detect(args: argparse.Namespace) -> None:
    from maritime.detection.pipeline import run_detection

    print(json.dumps(run_detection(seed=args.seed), indent=2, default=str))


def cmd_benchmark_eval(args: argparse.Namespace) -> None:
    from maritime.detection.evaluation import run_synthetic_benchmark

    print(json.dumps(run_synthetic_benchmark(), indent=2, default=str))


def cmd_network(args: argparse.Namespace) -> None:
    from maritime.networks.pipeline import build_and_save_network

    print(json.dumps(build_and_save_network(), indent=2, default=str))


def cmd_pipeline(args: argparse.Namespace) -> None:
    """Everything after download, in order."""
    for fn in (cmd_ingest, cmd_ports, cmd_process, cmd_detect, cmd_benchmark_eval, cmd_network):
        logger.info("=== %s ===", fn.__name__)
        fn(args)


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    p = argparse.ArgumentParser(prog="maritime")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_: str) -> argparse.ArgumentParser:  # noqa: ANN001
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)
        sp.add_argument("--start", help="YYYY-MM-DD (default: MIADP_STUDY_START)")
        sp.add_argument("--end", help="YYYY-MM-DD (default: MIADP_STUDY_END)")
        sp.add_argument("--files", nargs="*", help="explicit AIS files (zip/csv)")
        sp.add_argument("--file", help="explicit reference file")
        sp.add_argument("--buffer", type=float, default=0.5, help="port bbox buffer (deg)")
        sp.add_argument("--seed", type=int, default=42)
        sp.add_argument("--load", action="store_true")
        return sp

    add("download", cmd_download, "download NOAA AIS daily archives")
    add("ingest", cmd_ingest, "validate and load AIS files into PostGIS")
    add("ports", cmd_ports, "load the NGA World Port Index near the study region")
    add("synthetic", cmd_synthetic, "generate synthetic fixtures (and --load into the DB)")
    add("process", cmd_process, "trajectories, features, port proximity")
    add("detect", cmd_detect, "rule-based + Isolation Forest anomaly detection")
    add("evaluate", cmd_benchmark_eval, "synthetic labeled benchmark metrics")
    add("network", cmd_network, "build the vessel/port/region network")
    add("pipeline", cmd_pipeline, "ingest -> ports -> process -> detect -> evaluate -> network")
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
