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
- Creates sequential ActivityWatch timelines instead of overlapping events
- Writes matching `not-afk` events so the ActivityWatch UI keeps the imported data visible

## Repository Structure

- `main.py` - Main importer script
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
   - `aw_hostname` to your local ActivityWatch server host
   - `aw_port` to your local ActivityWatch server port
   - `aw_client_hostname` to the client name you want to use for bucket IDs
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
4. Imports each new day into ActivityWatch
5. Writes the newest successfully processed date to `sync_status.json`

If the log file contains malformed lines, the importer skips them and continues processing the rest of the file.
