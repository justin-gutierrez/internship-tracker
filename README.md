# Internship Application Tracker

Local Python CLI that reads your Gmail (via official API), classifies internship-related emails with a **local** LLM (Ollama), stores results in SQLite, and exports **Excel** with company, role, location, pay, and status.

## Privacy

- Gmail content is fetched with OAuth on your machine.
- Classification runs against **Ollama on localhost** — email bodies do not go to a cloud LLM.

## Prerequisites

1. **Python 3.10+**
2. **[Ollama](https://ollama.com)** installed and running:
   ```bash
   ollama pull llama3.1:8b
   ```
3. **Google Cloud**
   - Create a project, enable **Gmail API**.
   - Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen).
   - If the app is in **Testing** (typical for a personal Desktop client), you **must** add every Google account whose Gmail you will scrape under **Test users**. Use the **exact** email address you sign in with in the browser when OAuth runs. If that account is not listed, Google returns **`access_denied`** and OAuth will fail.
   - For **External** apps, add those accounts as test users; **Internal** (Google Workspace only) restricts who can use the app per your org.
   - Credentials → Create OAuth client ID → **Desktop app**.
   - Download JSON as `credentials.json` in the project root (same folder as `pyproject.toml`).

## Install

```bash
cd internship-tracker
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env        # optional; edit defaults
```

## Usage

```bash
# First run: full scan (OAuth opens in browser once)
tracker scan

# Limit scope
tracker scan --max-emails 500 --lookback-days 180

# Dry run: Gmail + prefilter only, no Ollama calls
tracker scan --dry-run

# Incremental: only new messages since last scan (uses Gmail history)
tracker refresh

# Export current DB to Excel without re-fetching Gmail
tracker export
```

Output defaults to `data/applications.xlsx` (configurable via `OUTPUT_PATH` or `--output`).

## Files

| File | Purpose |
|------|---------|
| `credentials.json` | From Google Cloud (Desktop OAuth client) — **gitignored** |
| `token.json` | Cached OAuth token — **gitignored** |
| `data/tracker.db` | SQLite cache — **gitignored** |
| `data/applications.xlsx` | Exported spreadsheet — **gitignored** |

## Status values

`applied`, `interview`, `offer`, `rejected`, `other` — combined per (company, role) with terminal states (`rejected`, `offer`) taking precedence.

## Tests

```bash
pytest tests/
```
