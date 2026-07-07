from __future__ import annotations

import inspect
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, DefaultDict, Iterable

try:
    from aw_client import ActivityWatchClient
    from aw_core.models import Event
except ImportError as exc:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "Missing dependencies. Install aw-client and aw-core before running this importer."
    ) from exc


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_CONFIG = {
    "log_file_path": r"C:\Users\flori\iCloudDrive\IPadUsageLogs\Florian_IPad_Daily-Usage-Time.txt",
    "aw_hostname": "127.0.0.1",
    "aw_port": 5600,
    "aw_client_hostname": "Florian_IPad_SimpleScreentime",
    "sync_status_file": "sync_status.json",
}

BLOCK_PATTERN = re.compile(
    r"(?ms)^\s*(\d{4}-\d{2}-\d{2})\s*:\s*\{\s*(.*?)\s*\}\s*$"
)
ENTRY_PATTERN = re.compile(r"^\s*(?P<app>.+)\s*\((?P<duration>[^()]*)\)\s*$")
DURATION_PATTERN = re.compile(
    r"(\d+)\s*(hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedEntry:
    app_name: str
    duration_seconds: int


def resolve_path(raw_value: str | Path, base_dir: Path) -> Path:
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Missing config.json at {CONFIG_FILE}. Copy config.example.json to config.json and customize it."
        )

    config = DEFAULT_CONFIG.copy()
    loaded = load_json_file(CONFIG_FILE)
    if not isinstance(loaded, dict):
        raise ValueError("config.json must contain a JSON object.")

    config.update(loaded)
    return config


def parse_duration_to_seconds(duration_text: str) -> int | None:
    total_seconds = 0
    matched = False

    for amount_text, unit_text in DURATION_PATTERN.findall(duration_text):
        matched = True
        amount = int(amount_text)
        unit = unit_text.lower()

        if unit.startswith(("hour", "hr")) or unit == "h":
            total_seconds += amount * 3600
        elif unit.startswith(("minute", "min")) or unit == "m":
            total_seconds += amount * 60
        else:
            total_seconds += amount

    if not matched or total_seconds <= 0:
        return None

    return total_seconds


def parse_block_entries(block_body: str) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []

    for raw_line in block_body.splitlines():
        line = raw_line.strip().rstrip("}")
        if not line:
            continue

        match = ENTRY_PATTERN.match(line)
        if not match:
            continue

        app_name = match.group("app").strip()
        duration_text = match.group("duration").strip()
        if not app_name or not duration_text:
            continue

        duration_seconds = parse_duration_to_seconds(duration_text)
        if duration_seconds is None:
            continue

        entries.append(ParsedEntry(app_name=app_name, duration_seconds=duration_seconds))

    return entries


def parse_log_file(log_path: Path) -> dict[str, list[ParsedEntry]]:
    try:
        content = log_path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Log file not found: {log_path}") from exc

    grouped_entries: DefaultDict[str, list[ParsedEntry]] = defaultdict(list)

    for date_text, block_body in BLOCK_PATTERN.findall(content):
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            continue

        entries = parse_block_entries(block_body)
        if entries:
            grouped_entries[date_text].extend(entries)

    return dict(grouped_entries)


def load_last_synced_date(sync_status_path: Path) -> str | None:
    if not sync_status_path.exists():
        return None

    try:
        payload = load_json_file(sync_status_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    value = payload.get("last_synced_date")
    if not isinstance(value, str):
        return None

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None

    return value


def save_last_synced_date(sync_status_path: Path, date_text: str) -> None:
    save_json_file(sync_status_path, {"last_synced_date": date_text})


def build_activitywatch_client(client_name: str, hostname: str, port: int) -> ActivityWatchClient:
    attempts: list[dict[str, Any]] = [
        {"host": hostname, "port": port},
        {"hostname": hostname, "port": port},
        {"server": hostname, "port": port},
        {"base_url": f"http://{hostname}:{port}"},
        {"url": f"http://{hostname}:{port}"},
        {"host": hostname},
        {"hostname": hostname},
        {},
    ]

    last_error: Exception | None = None
    for extra_kwargs in attempts:
        try:
            return ActivityWatchClient(client_name, **extra_kwargs)
        except TypeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error

    return ActivityWatchClient(client_name)


def ensure_bucket(client: Any, bucket_id: str, bucket_type: str) -> None:
    method = getattr(client, "create_bucket", None) or getattr(client, "create", None)
    if method is None:
        raise RuntimeError("The installed ActivityWatch client does not expose a bucket creation method.")

    signature = inspect.signature(method)
    kwargs: dict[str, Any] = {}

    if "event_type" in signature.parameters:
        kwargs["event_type"] = bucket_type
    elif "bucket_type" in signature.parameters:
        kwargs["bucket_type"] = bucket_type
    elif "type" in signature.parameters:
        kwargs["type"] = bucket_type

    try:
        if kwargs:
            try:
                method(bucket_id, **kwargs)
                return
            except TypeError:
                method(bucket_id, bucket_type)
                return
        else:
            method(bucket_id, bucket_type)
    except Exception as exc:  # Bucket may already exist; ignore that case.
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        if status_code == 409 or "already exists" in message or "exists" in message:
            return
        raise


def insert_events(client: Any, bucket_id: str, events: list[Event]) -> None:
    method = (
        getattr(client, "insert_events", None)
        or getattr(client, "insert", None)
        or getattr(client, "post_events", None)
    )
    if method is None:
        raise RuntimeError("The installed ActivityWatch client does not expose an event insertion method.")

    signature = inspect.signature(method)
    parameters = [
        name for name in signature.parameters if name not in {"self", "cls"}
    ]
    call_errors: list[Exception] = []

    if parameters[:2] == ["bucket_id", "events"]:
        try:
            method(bucket_id, events)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if parameters[:2] == ["events", "bucket_id"]:
        try:
            method(events, bucket_id)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if "bucket_id" in signature.parameters and "events" in signature.parameters:
        try:
            method(bucket_id=bucket_id, events=events)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if len(parameters) >= 2:
        try:
            method(bucket_id, events)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if call_errors:
        raise call_errors[-1]

    raise RuntimeError("The installed ActivityWatch client does not accept event insertion arguments.")


def create_events_for_day(day_text: str, entries: Iterable[ParsedEntry]) -> tuple[list[Event], list[Event]]:
    day_start = datetime.strptime(day_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    current_start = day_start

    app_events: list[Event] = []
    afk_events: list[Event] = []

    for entry in entries:
        duration = timedelta(seconds=entry.duration_seconds)
        app_events.append(
            Event(
                current_start,
                duration,
                {
                    "app": entry.app_name,
                    "title": entry.app_name,
                    "usage_seconds": entry.duration_seconds,
                },
            )
        )
        afk_events.append(
            Event(current_start, duration, {"status": "not-afk"})
        )
        current_start += duration

    return app_events, afk_events


def sync_days(
    client: Any,
    grouped_entries: dict[str, list[ParsedEntry]],
    last_synced_date: str | None,
    client_hostname: str,
    sync_status_path: Path,
) -> tuple[int, str | None]:
    app_bucket_id = f"aw-watcher-window_{client_hostname}"
    afk_bucket_id = f"aw-watcher-afk_{client_hostname}"

    ensure_bucket(client, app_bucket_id, "currentwindow")
    ensure_bucket(client, afk_bucket_id, "afkstatus")

    processed_dates = 0
    last_synced_value = last_synced_date

    for date_text in sorted(grouped_entries):
        if last_synced_value is not None and date_text <= last_synced_value:
            continue

        app_events, afk_events = create_events_for_day(date_text, grouped_entries[date_text])
        if not app_events:
            continue

        insert_events(client, app_bucket_id, app_events)
        insert_events(client, afk_bucket_id, afk_events)

        last_synced_value = date_text
        save_last_synced_date(sync_status_path, last_synced_value)
        processed_dates += 1

    return processed_dates, last_synced_value


def main() -> int:
    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    log_file_path = resolve_path(config["log_file_path"], BASE_DIR)
    sync_status_path = resolve_path(config["sync_status_file"], BASE_DIR)
    aw_hostname = str(config["aw_hostname"])

    try:
        aw_port = int(config["aw_port"])
    except (TypeError, ValueError):
        print("Configuration error: aw_port must be an integer.", file=sys.stderr)
        return 1

    aw_client_hostname = str(config["aw_client_hostname"])

    try:
        grouped_entries = parse_log_file(log_file_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to parse log file: {exc}", file=sys.stderr)
        return 1

    if not grouped_entries:
        print("No valid daily usage blocks were found.")
        return 0

    last_synced_date = load_last_synced_date(sync_status_path)

    try:
        client = build_activitywatch_client(aw_client_hostname, aw_hostname, aw_port)
    except Exception as exc:
        print(f"Failed to initialize ActivityWatch client: {exc}", file=sys.stderr)
        return 1

    try:
        processed_dates, _ = sync_days(
            client=client,
            grouped_entries=grouped_entries,
            last_synced_date=last_synced_date,
            client_hostname=aw_client_hostname,
            sync_status_path=sync_status_path,
        )
    except Exception as exc:
        print(f"Failed to synchronize with ActivityWatch: {exc}", file=sys.stderr)
        return 1
    finally:
        close_method = getattr(client, "close", None) or getattr(client, "shutdown", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:
                pass

    print(f"Imported {processed_dates} day(s) into ActivityWatch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
