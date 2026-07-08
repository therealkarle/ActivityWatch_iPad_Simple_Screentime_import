# iPad Screen Time to ActivityWatch Importer

## Project Overview

This project imports aggregated daily usage data from an iPad Screen Time log file stored in iCloud Drive into a local ActivityWatch instance on Windows.

The importer reads a structured text log, converts each app duration into ActivityWatch events, writes a matching AFK event for the same time span, and persists the last successfully processed date so repeated runs only import new history.

## Features

- Reads the Screen Time log from a configurable local path
- Parses daily blocks in the format `YYYY-MM-DD:{ ... },` with an optional trailing comma after each block
- Converts mixed durations such as `3h 24min`, `17min`, and `10s` into seconds
- Skips malformed rows without stopping the import
- Stores the last successful sync date in `sync_status.json`
- Applies configurable screen-time discount factors before planning and import
- Plans each day across configurable time windows, minimizes app-block splits, and keeps any overflow in the same-day schedule instead of exporting a separate overflow block
- Writes matching `not-afk` events so the ActivityWatch UI keeps the imported data visible

## Repository Structure

- `main.py` - Main importer script
- `reset.bat` - Windows helper that deletes the imported ActivityWatch buckets and sync state
- `config.json` - Local configuration file for private use
- `config.example.json` - Public template configuration
- `.gitignore` - Ignore rules for local secrets, sync state, and private logs
- `README.md` - Setup and usage documentation

## Prerequisites

- Python 3.10 or newer
- A running local ActivityWatch server
- The `aw-client` and `aw-core` Python packages

Install the required packages with:

```powershell
python -m pip install --upgrade pip
python -m pip install aw-client aw-core
```

## Configuration Setup

1. Copy `config.example.json` to `config.json` if you do not already have a local configuration file.
2. Edit `config.json` and set:
   - `log_file_path` to the full path of your iCloud Drive Screen Time log
   - `activitywatch_base_url` to your local ActivityWatch server URL
   - `activitywatch_hostname` to the client name you want to use for bucket IDs
   - `activitywatch_bucket_hostname` to the host name that should own the imported buckets in ActivityWatch
   - `activitywatch_app_name_suffix` to the suffix appended to every imported app name, for example ` - FlorianIPad`
   - `activitywatch_app_name_suffix_overrides` to a JSON object that maps exact raw app names to custom suffixes, for example `{"Safari": " - Private"}`
   - `activitywatch_app_discount_factor` to the global multiplier applied to every app duration before planning, for example `0.8`
   - `activitywatch_app_discount_factor_overrides` to a JSON object that maps exact raw app names to custom multipliers, for example `{"Safari": 0.75}`
   - `start_time` to the first local clock time the synthetic day may start, using `HHMM` notation such as `0` or `600`
   - `wake_up_time` to the local time where the importer should start preferring backup windows, also in `HHMM`
   - `backup_intervals` to a semicolon-separated list like `[2200;2400]; [1200;1300]`
   - `sync_status_file` to the state file path, if you want a different location
   - `debug` to `true` if you want detailed diagnostic output, or `false` for normal runs
3. Keep `config.json` out of version control. It is already ignored by `.gitignore`.

## Usage

Run the importer from the repository root:

```powershell
python main.py
```

On each run, the script:

1. Reads `config.json`
2. Loads the Screen Time log file
3. Skips all dates that are less than or equal to the last synced date
4. Computes the discounted total screentime for each new day, then fills the configured windows in order while minimizing splits
5. Imports each new day into ActivityWatch
6. Writes the newest successfully processed date to `sync_status.json`

If the log file contains malformed lines, the importer skips them and continues processing the rest of the file.

Window planning works like this:

1. The importer first fills the main window from `start_time` to `wake_up_time`
2. Then it tries the configured `backup_intervals` in the order you listed them
3. The planner looks at the discounted day total up front and prefers to keep app blocks intact whenever a later window can hold them completely
4. Backup windows can keep up to 10 minutes of slack unused when that reduces splits in the final plan
5. If the full day does not fit inside the configured windows, the planner fills the fallback windows first, then extends the morning window after `wake_up_time`
6. The start block may be split so fallback windows can be filled before the morning extension is used
7. No event is planned past midnight; if the day still does not fit, the importer raises an error instead of moving time into the next day

To reset the importer state on Windows, run:

```powershell
reset.bat
```
