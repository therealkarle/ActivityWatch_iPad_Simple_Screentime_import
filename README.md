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
- Plans each day across configurable time windows and minimizes app-block splits
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
   - `start_time` to the first clock time the synthetic day may start, using `HHMM` notation such as `0` or `600`
   - `wake_up_time` to the point where the importer should start preferring backup windows, also in `HHMM`
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
4. Computes the total screentime for each new day, then fills the configured windows in order
5. Imports each new day into ActivityWatch
6. Writes the newest successfully processed date to `sync_status.json`

If the log file contains malformed lines, the importer skips them and continues processing the rest of the file.

Window planning works like this:

1. The importer first fills the main window from `start_time` to `wake_up_time`
2. Then it tries the configured `backup_intervals` in the order you listed them
3. If a block does not fit in the current window, the planner prefers a later window that can hold it completely
4. Only if no later window can hold the block intact does it split that block
5. Remaining time falls back into the rest of the day after `wake_up_time`

To reset the importer state on Windows, run:

```powershell
reset.bat
```
