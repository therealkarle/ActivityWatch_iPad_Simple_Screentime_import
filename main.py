from __future__ import annotations

import inspect
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
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
LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc
DEFAULT_CONFIG = {
    "log_file_path": r"C:\Users\flori\iCloudDrive\IPadUsageLogs\Florian_IPad_Daily-Usage-Time.txt",
    "activitywatch_base_url": "http://127.0.0.1:5600",
    "activitywatch_hostname": "Florian_IPad_SimpleScreentime",
    "activitywatch_bucket_hostname": None,
    "start_time": 0,
    "wake_up_time": 600,
    "backup_intervals": "",
    "sync_status_file": "sync_status.json",
    "debug": False,
}

BLOCK_PATTERN = re.compile(
    # Match repeated blocks like `2026-07-06:{ ... },` anywhere in the file.
    r"(?ms)(\d{4}-\d{2}-\d{2})\s*:\s*\{\s*(.*?)\s*\}\s*,?"
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


@dataclass(frozen=True)
class TimeWindow:
    label: str
    start: datetime
    end: datetime

    @property
    def capacity_seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))


@dataclass(frozen=True)
class PlannedSegment:
    app_name: str
    start: datetime
    duration_seconds: int


def log_debug(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[debug] {message}")


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


def get_config_value(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in config:
            return config[key]
    return default


def get_debug_flag(config: dict[str, Any]) -> bool:
    value = config.get("debug", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def parse_clock_minutes(value: Any, *, allow_2400: bool = False, field_name: str = "time") -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a clock value, not a boolean.")

    raw_text: str
    if isinstance(value, int):
        raw_text = str(value)
    elif isinstance(value, str):
        raw_text = value.strip()
    else:
        raise ValueError(f"{field_name} must be a number or string in HHMM format.")

    if not raw_text:
        raise ValueError(f"{field_name} must not be empty.")

    if ":" in raw_text:
        parts = raw_text.split(":")
        if len(parts) != 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
            raise ValueError(f"{field_name} must use HH:MM or HHMM notation.")
        hours = int(parts[0])
        minutes = int(parts[1])
    else:
        if not raw_text.isdigit():
            raise ValueError(f"{field_name} must use HHMM notation.")
        padded = raw_text.zfill(4)
        hours = int(padded[:-2])
        minutes = int(padded[-2:])

    if hours == 24 and minutes == 0 and allow_2400:
        return 24 * 60

    if hours < 0 or hours > 23:
        raise ValueError(f"{field_name} hour must be between 0 and 23.")
    if minutes < 0 or minutes > 59:
        raise ValueError(f"{field_name} minute must be between 0 and 59.")

    return hours * 60 + minutes


def parse_backup_intervals(raw_value: Any) -> list[tuple[int, int]]:
    if raw_value is None:
        return []

    if isinstance(raw_value, (list, tuple)):
        if len(raw_value) == 0:
            return []
        intervals: list[tuple[int, int]] = []
        for index, item in enumerate(raw_value, start=1):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError(
                    "backup_intervals must contain pairs like [2200, 2400] or strings like [2200;2400]."
                )
            start = parse_clock_minutes(item[0], field_name=f"backup_intervals[{index}].start")
            end = parse_clock_minutes(item[1], allow_2400=True, field_name=f"backup_intervals[{index}].end")
            intervals.append((start, end))
    else:
        text = str(raw_value).strip()
        if not text:
            return []

        matches = list(re.finditer(r"\[\s*([^;\]]+)\s*;\s*([^\]]+)\s*\]", text))
        if not matches:
            raise ValueError(
                "backup_intervals must use the format [2200;2400]; [1200;1300]."
            )

        intervals = []
        for index, match in enumerate(matches, start=1):
            start_text = match.group(1).strip()
            end_text = match.group(2).strip()
            start = parse_clock_minutes(start_text, field_name=f"backup_intervals[{index}].start")
            end = parse_clock_minutes(end_text, allow_2400=True, field_name=f"backup_intervals[{index}].end")
            intervals.append((start, end))

    normalized: list[tuple[int, int]] = []
    for index, (start, end) in enumerate(intervals, start=1):
        if start >= end:
            raise ValueError(f"backup_intervals[{index}] must have a start that is earlier than the end.")
        if start < 0 or start >= 24 * 60:
            raise ValueError(f"backup_intervals[{index}].start must be within the day.")
        if end < 0 or end > 24 * 60:
            raise ValueError(f"backup_intervals[{index}].end must be within the day.")
        normalized.append((start, end))

    for left_index, (left_start, left_end) in enumerate(normalized):
        for right_index in range(left_index + 1, len(normalized)):
            right_start, right_end = normalized[right_index]
            if left_start < right_end and right_start < left_end:
                raise ValueError("backup_intervals must not overlap.")

    return normalized


def minutes_to_clock_label(minutes: int) -> str:
    if minutes == 24 * 60:
        return "24:00"
    hours, remainder = divmod(minutes, 60)
    return f"{hours:02d}:{remainder:02d}"


def build_time_window(day_start: datetime, start_minute: int, end_minute: int, label: str) -> TimeWindow:
    start = day_start + timedelta(minutes=start_minute)
    end = day_start + timedelta(minutes=end_minute)
    return TimeWindow(label=label, start=start, end=end)


def build_planning_windows(
    day_text: str,
    start_time: int,
    wake_up_time: int,
    backup_intervals: list[tuple[int, int]],
) -> list[TimeWindow]:
    if start_time < 0 or start_time >= 24 * 60:
        raise ValueError("start_time must be within the day.")
    if wake_up_time <= start_time or wake_up_time > 24 * 60:
        raise ValueError("wake_up_time must be later than start_time and within the day.")

    day_start = datetime.strptime(day_text, "%Y-%m-%d").replace(tzinfo=LOCAL_TIMEZONE)
    windows: list[TimeWindow] = []

    windows.append(
        build_time_window(
            day_start,
            start_time,
            wake_up_time,
            f"primary:{minutes_to_clock_label(start_time)}-{minutes_to_clock_label(wake_up_time)}",
        )
    )

    for index, (start_minute, end_minute) in enumerate(backup_intervals, start=1):
        if start_minute < 0 or end_minute > 24 * 60:
            raise ValueError(f"backup_intervals[{index}] must stay within a single day.")
        if not (end_minute <= start_time or start_minute >= wake_up_time):
            raise ValueError("backup_intervals must not overlap the primary window.")
        windows.append(
            build_time_window(
                day_start,
                start_minute,
                end_minute,
                f"backup:{minutes_to_clock_label(start_minute)}-{minutes_to_clock_label(end_minute)}",
            )
        )

    relevant_backups = [
        (max(wake_up_time, start_minute), min(24 * 60, end_minute))
        for start_minute, end_minute in backup_intervals
        if end_minute > wake_up_time
    ]
    relevant_backups.sort()

    cursor = wake_up_time
    for start_minute, end_minute in relevant_backups:
        if start_minute > cursor:
            windows.append(
                build_time_window(
                    day_start,
                    cursor,
                    start_minute,
                    f"fallback:{minutes_to_clock_label(cursor)}-{minutes_to_clock_label(start_minute)}",
                )
            )
        cursor = max(cursor, end_minute)

    if cursor < 24 * 60:
        windows.append(
            build_time_window(
                day_start,
                cursor,
                24 * 60,
                f"fallback:{minutes_to_clock_label(cursor)}-24:00",
            )
        )

    # Drop any zero-length windows from degenerate configurations.
    return [window for window in windows if window.capacity_seconds > 0]


def plan_entries_into_windows(entries: list[ParsedEntry], windows: list[TimeWindow], debug: bool = False) -> list[PlannedSegment]:
    if not entries or not windows:
        return []

    total_duration = sum(entry.duration_seconds for entry in entries)
    total_capacity = sum(window.capacity_seconds for window in windows)
    log_debug(debug, f"Total screentime: {total_duration} seconds")
    log_debug(debug, f"Available window capacity: {total_capacity} seconds")

    if total_duration > total_capacity:
        raise ValueError(
            "The configured time windows do not provide enough capacity for the day "
            f"({total_duration} seconds required, {total_capacity} seconds available)."
        )

    segments: list[PlannedSegment] = []
    remaining_entries: list[dict[str, Any]] = [
        {"app_name": entry.app_name, "remaining_seconds": entry.duration_seconds}
        for entry in entries
    ]
    future_capacity_ceiling = [0] * (len(windows) + 1)
    for index in range(len(windows) - 1, -1, -1):
        future_capacity_ceiling[index] = max(windows[index].capacity_seconds, future_capacity_ceiling[index + 1])

    for window_index, window in enumerate(windows):
        window_remaining = window.capacity_seconds
        cursor = window.start

        while window_remaining > 0 and remaining_entries:
            current_entry = remaining_entries[0]
            current_remaining = int(current_entry["remaining_seconds"])

            if current_remaining <= window_remaining:
                segments.append(
                    PlannedSegment(
                        app_name=str(current_entry["app_name"]),
                        start=cursor,
                        duration_seconds=current_remaining,
                    )
                )
                cursor += timedelta(seconds=current_remaining)
                window_remaining -= current_remaining
                remaining_entries.pop(0)
                log_debug(
                    debug,
                    f"Placed current block intact in {window.label}: {current_entry['app_name']} ({current_remaining}s)",
                )
                continue

            fitting_candidates = [
                (index, int(entry["remaining_seconds"]))
                for index, entry in enumerate(remaining_entries[1:], start=1)
                if int(entry["remaining_seconds"]) <= window_remaining
            ]

            if fitting_candidates:
                candidate_index, candidate_seconds = min(
                    fitting_candidates,
                    key=lambda item: (item[1], item[0]),
                )
                candidate_entry = remaining_entries.pop(candidate_index)
                segments.append(
                    PlannedSegment(
                        app_name=str(candidate_entry["app_name"]),
                        start=cursor,
                        duration_seconds=candidate_seconds,
                    )
                )
                cursor += timedelta(seconds=candidate_seconds)
                window_remaining -= candidate_seconds
                log_debug(
                    debug,
                    f"Filled gap in {window.label} with later block: {candidate_entry['app_name']} ({candidate_seconds}s)",
                )
                continue

            if current_remaining <= future_capacity_ceiling[window_index + 1]:
                log_debug(
                    debug,
                    f"Left gap open in {window.label} so block can stay intact later: "
                    f"{current_entry['app_name']} ({current_remaining}s)",
                )
                break

            split_seconds = min(current_remaining, window_remaining)
            segments.append(
                PlannedSegment(
                    app_name=str(current_entry["app_name"]),
                    start=cursor,
                    duration_seconds=split_seconds,
                )
            )
            cursor += timedelta(seconds=split_seconds)
            window_remaining -= split_seconds
            current_entry["remaining_seconds"] = current_remaining - split_seconds
            log_debug(
                debug,
                f"Split block in {window.label}: {current_entry['app_name']} ({split_seconds}s of {current_remaining}s)",
            )
            if int(current_entry["remaining_seconds"]) <= 0:
                remaining_entries.pop(0)

    return segments


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
        line = raw_line.strip().rstrip("},")
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


def parse_log_file(log_path: Path, debug: bool = False) -> dict[str, list[ParsedEntry]]:
    try:
        content = log_path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Log file not found: {log_path}") from exc

    log_debug(debug, f"Reading log file: {log_path}")
    log_debug(debug, f"Log file size: {len(content)} characters")

    grouped_entries: DefaultDict[str, list[ParsedEntry]] = defaultdict(list)
    matches = list(BLOCK_PATTERN.findall(content))
    log_debug(debug, f"Found {len(matches)} daily block candidate(s)")

    for date_text, block_body in matches:
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            log_debug(debug, f"Skipping invalid date block: {date_text}")
            continue

        entries = parse_block_entries(block_body)
        if entries:
            grouped_entries[date_text].extend(entries)
            log_debug(debug, f"Parsed {date_text}: {len(entries)} valid entrie(s)")
        else:
            log_debug(debug, f"Parsed {date_text}: no valid entries")

    return dict(grouped_entries)


def load_last_synced_date(sync_status_path: Path, debug: bool = False) -> str | None:
    if not sync_status_path.exists():
        log_debug(debug, f"Sync status file not found: {sync_status_path}")
        return None

    try:
        payload = load_json_file(sync_status_path)
    except (OSError, json.JSONDecodeError, ValueError):
        log_debug(debug, f"Could not read sync status file: {sync_status_path}")
        return None

    if not isinstance(payload, dict):
        log_debug(debug, "Sync status file does not contain a JSON object")
        return None

    value = payload.get("last_synced_date")
    if not isinstance(value, str):
        log_debug(debug, "Sync status file is missing last_synced_date")
        return None

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        log_debug(debug, f"Sync status has invalid date: {value}")
        return None

    log_debug(debug, f"Last synced date: {value}")
    return value


def save_last_synced_date(sync_status_path: Path, date_text: str, debug: bool = False) -> None:
    save_json_file(sync_status_path, {"last_synced_date": date_text})
    log_debug(debug, f"Updated sync status file: {sync_status_path} -> {date_text}")


def build_activitywatch_client(
    client_name: str,
    base_url: str,
    debug: bool = False,
) -> ActivityWatchClient:
    attempts: list[dict[str, Any]] = [{"base_url": base_url}, {"url": base_url}]

    parsed_url = urlparse(base_url)
    hostname = parsed_url.hostname
    port = parsed_url.port
    if hostname:
        if port is not None:
            attempts.extend(
                [
                    {"host": hostname, "port": port},
                    {"hostname": hostname, "port": port},
                    {"server": hostname, "port": port},
                ]
            )
        attempts.extend(
            [
                {"host": hostname},
                {"hostname": hostname},
                {"server": hostname},
            ]
        )

    attempts.append({})

    last_error: Exception | None = None
    for extra_kwargs in attempts:
        try:
            client = ActivityWatchClient(client_name, **extra_kwargs)
            log_debug(debug, f"Initialized ActivityWatch client with args: {extra_kwargs or '{}'}")
            return client
        except TypeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error

    return ActivityWatchClient(client_name)


def apply_bucket_hostname_override(client: Any, bucket_hostname: str | None, debug: bool = False) -> None:
    if not bucket_hostname:
        return

    if hasattr(client, "client_hostname"):
        client.client_hostname = bucket_hostname
        log_debug(debug, f"ActivityWatch bucket hostname override: {bucket_hostname}")


def ensure_bucket(client: Any, bucket_id: str, bucket_type: str, debug: bool = False) -> None:
    method = getattr(client, "create_bucket", None) or getattr(client, "create", None)
    if method is None:
        raise RuntimeError("The installed ActivityWatch client does not expose a bucket creation method.")

    log_debug(debug, f"Ensuring bucket exists: {bucket_id} ({bucket_type})")
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
            log_debug(debug, f"Bucket already exists: {bucket_id}")
            return
        raise


def insert_events(client: Any, bucket_id: str, events: list[Event], debug: bool = False) -> None:
    method = (
        getattr(client, "insert_events", None)
        or getattr(client, "insert", None)
        or getattr(client, "post_events", None)
    )
    if method is None:
        raise RuntimeError("The installed ActivityWatch client does not expose an event insertion method.")

    log_debug(debug, f"Inserting {len(events)} event(s) into {bucket_id}")
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


def create_events_for_day(
    day_text: str,
    entries: Iterable[ParsedEntry],
    *,
    start_time: int,
    wake_up_time: int,
    backup_intervals: list[tuple[int, int]],
    debug: bool = False,
) -> tuple[list[Event], list[Event]]:
    entry_list = list(entries)
    if not entry_list:
        return [], []

    planning_windows = build_planning_windows(day_text, start_time, wake_up_time, backup_intervals)
    planned_segments = plan_entries_into_windows(entry_list, planning_windows, debug=debug)

    app_events: list[Event] = []
    afk_events: list[Event] = []

    for segment in planned_segments:
        duration = timedelta(seconds=segment.duration_seconds)
        app_events.append(
            Event(
                timestamp=segment.start,
                duration=duration,
                data={
                    "app": segment.app_name,
                    "title": segment.app_name,
                    "usage_seconds": segment.duration_seconds,
                },
            )
        )
        afk_events.append(
            Event(
                timestamp=segment.start,
                duration=duration,
                data={"status": "not-afk"},
            )
        )

    app_events.sort(key=lambda event: event.timestamp)
    afk_events.sort(key=lambda event: event.timestamp)
    return app_events, afk_events


def sync_days(
    client: Any,
    grouped_entries: dict[str, list[ParsedEntry]],
    last_synced_date: str | None,
    client_hostname: str,
    sync_status_path: Path,
    start_time: int,
    wake_up_time: int,
    backup_intervals: list[tuple[int, int]],
    debug: bool = False,
) -> tuple[int, str | None]:
    app_bucket_id = f"aw-watcher-window_{client_hostname}"
    afk_bucket_id = f"aw-watcher-afk_{client_hostname}"

    log_debug(debug, f"Target bucket IDs: {app_bucket_id}, {afk_bucket_id}")
    ensure_bucket(client, app_bucket_id, "currentwindow", debug=debug)
    ensure_bucket(client, afk_bucket_id, "afkstatus", debug=debug)

    processed_dates = 0
    last_synced_value = last_synced_date

    for date_text in sorted(grouped_entries):
        if last_synced_value is not None and date_text <= last_synced_value:
            log_debug(debug, f"Skipping already-synced day: {date_text}")
            continue

        app_events, afk_events = create_events_for_day(
            date_text,
            grouped_entries[date_text],
            start_time=start_time,
            wake_up_time=wake_up_time,
            backup_intervals=backup_intervals,
            debug=debug,
        )
        if not app_events:
            log_debug(debug, f"Skipping day with no app events: {date_text}")
            continue

        log_debug(debug, f"Importing {date_text}: {len(app_events)} app event(s), {len(afk_events)} afk event(s)")
        insert_events(client, app_bucket_id, app_events, debug=debug)
        insert_events(client, afk_bucket_id, afk_events, debug=debug)

        last_synced_value = date_text
        save_last_synced_date(sync_status_path, last_synced_value, debug=debug)
        processed_dates += 1

    return processed_dates, last_synced_value


def main() -> int:
    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    debug = get_debug_flag(config)

    log_file_path = resolve_path(config["log_file_path"], BASE_DIR)
    sync_status_path = resolve_path(config["sync_status_file"], BASE_DIR)
    aw_base_url = str(
        get_config_value(
            config,
            "activitywatch_base_url",
            "aw_base_url",
            default=f"http://{get_config_value(config, 'aw_hostname', default='127.0.0.1')}:{get_config_value(config, 'aw_port', default=5600)}",
        )
    )
    aw_client_hostname = str(
        get_config_value(
            config,
            "activitywatch_hostname",
            "aw_client_hostname",
            default="Florian_IPad_SimpleScreentime",
        )
    )
    aw_bucket_hostname = get_config_value(
        config,
        "activitywatch_bucket_hostname",
        "aw_bucket_hostname",
        default=None,
    )
    start_time = parse_clock_minutes(get_config_value(config, "start_time", default=0), field_name="start_time")
    wake_up_time = parse_clock_minutes(
        get_config_value(config, "wake_up_time", default=600),
        field_name="wake_up_time",
    )
    backup_intervals = parse_backup_intervals(get_config_value(config, "backup_intervals", default=[]))
    if aw_bucket_hostname is not None:
        aw_bucket_hostname = str(aw_bucket_hostname)
    log_debug(debug, f"Resolved log path: {log_file_path}")
    log_debug(debug, f"Resolved sync status path: {sync_status_path}")
    log_debug(debug, f"ActivityWatch base URL: {aw_base_url}")
    log_debug(debug, f"ActivityWatch client hostname: {aw_client_hostname}")
    log_debug(debug, f"Screen time start: {minutes_to_clock_label(start_time)}")
    log_debug(debug, f"Wake up time: {minutes_to_clock_label(wake_up_time)}")
    log_debug(debug, f"Backup intervals: {backup_intervals}")
    if aw_bucket_hostname is not None:
        log_debug(debug, f"ActivityWatch bucket hostname: {aw_bucket_hostname}")

    try:
        grouped_entries = parse_log_file(log_file_path, debug=debug)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to parse log file: {exc}", file=sys.stderr)
        return 1

    if not grouped_entries:
        print("No valid daily usage blocks were found.")
        return 0

    log_debug(debug, f"Parsed {len(grouped_entries)} day block(s) from log")
    last_synced_date = load_last_synced_date(sync_status_path, debug=debug)

    try:
        client = build_activitywatch_client(aw_client_hostname, aw_base_url, debug=debug)
    except Exception as exc:
        print(f"Failed to initialize ActivityWatch client: {exc}", file=sys.stderr)
        return 1

    apply_bucket_hostname_override(client, aw_bucket_hostname, debug=debug)

    try:
        processed_dates, _ = sync_days(
            client=client,
            grouped_entries=grouped_entries,
            last_synced_date=last_synced_date,
            client_hostname=aw_client_hostname,
            sync_status_path=sync_status_path,
            start_time=start_time,
            wake_up_time=wake_up_time,
            backup_intervals=backup_intervals,
            debug=debug,
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
    log_debug(debug, "Import run completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
