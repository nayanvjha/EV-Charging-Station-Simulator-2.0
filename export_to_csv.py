import argparse
import csv
from typing import List, Dict, Any

from db import get_station_history, list_sessions, init_db


def write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export station logs and sessions to CSV.")
    parser.add_argument("--station-id", help="Station ID to export logs/energy snapshots")
    parser.add_argument("--logs-out", default="station_logs.csv", help="CSV path for logs")
    parser.add_argument(
        "--snapshots-out",
        default="energy_snapshots.csv",
        help="CSV path for energy snapshots",
    )
    parser.add_argument("--sessions-out", default="sessions.csv", help="CSV path for sessions")
    parser.add_argument("--sessions-limit", type=int, default=200, help="Max sessions to export")
    args = parser.parse_args()

    init_db()

    if args.station_id:
        history = get_station_history(args.station_id)
        logs = history.get("logs", [])
        snapshots = history.get("energy_snapshots", [])
        if logs:
            write_csv(args.logs_out, logs, ["timestamp", "station_id", "message"])
        if snapshots:
            write_csv(
                args.snapshots_out,
                snapshots,
                ["timestamp", "station_id", "energy_delivered_kwh"],
            )

    sessions = list_sessions(limit=args.sessions_limit)
    if sessions:
        write_csv(
            args.sessions_out,
            sessions,
            ["session_id", "station_id", "start_time", "stop_time", "energy_kwh"],
        )


if __name__ == "__main__":
    main()
