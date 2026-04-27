# Windows + PyCharm Community 2025.x — Quick Start

This guide walks you through running `quant-scalper` from a fresh Windows PC
using **PyCharm Community Edition 2025.2.x**. Total time ≈ 5–10 minutes.

---

## 0. One-time prerequisites

* **Python 3.10 or newer** — install from <https://www.python.org/downloads/>.
  When the installer asks, **tick "Add Python to PATH"** before clicking
  *Install Now*.
* **PyCharm Community Edition 2025.2.x** — already installed per your spec.
* (Optional) **Git for Windows** if you want to `git pull` updates later.

---

## 1. Unzip the project

Right-click `quant-scalper.zip` → **Extract All…** → pick a path *without spaces*,
e.g. `C:\projects\quant-scalper`. Avoid OneDrive / 中文路径 (some Python tools
misbehave with non-ASCII paths).

---

## 2. Open in PyCharm

1. Launch PyCharm. On the welcome screen click **Open**.
2. Browse to `C:\projects\quant-scalper` and click **OK**.
3. PyCharm will index the project. Ignore the "no interpreter configured"
   warning for now — we'll set it up next.

---

## 3. Create the virtual environment

### Option A — one-click `.bat` (recommended)

Open Windows Explorer in `C:\projects\quant-scalper` and **double-click**
`scripts\setup.bat`. It creates `.venv\`, installs all dependencies, copies
`.env.example → .env`, and runs the test suite.

When it finishes you should see `7 passed in 0.4s`.

### Option B — let PyCharm do it

1. **File → Settings → Project: quant-scalper → Python Interpreter**
2. Click the gear → **Add… → Virtualenv Environment → New**
   * Location: `C:\projects\quant-scalper\.venv`
   * Base interpreter: your Python 3.10+
   * **Tick "Inherit global site-packages"**: NO (leave unticked)
3. Click **OK**, wait for venv creation.
4. Open PyCharm's terminal (bottom toolbar → **Terminal**) and run:

   ```powershell
   .\.venv\Scripts\activate
   pip install --upgrade pip
   pip install -r requirements-dev.txt
   pytest -q
   ```

---

## 4. Configure your Binance API keys

1. Edit `.env` (created in step 3 from `.env.example`).
2. Get a **Binance Futures testnet** key first (zero risk):
   <https://testnet.binancefuture.com/en/futures/BTCUSDT>
   → top-right person icon → *API Key* → *Create*.
3. Paste:
   ```ini
   QS_MODE=testnet
   BINANCE_API_KEY=<your testnet key>
   BINANCE_API_SECRET=<your testnet secret>
   ```
4. (Optional) Telegram — create a bot with `@BotFather`, get the token; send
   one message to your bot, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat id.
   ```ini
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<id>
   ```

---

## 5. Run a backtest

* Easiest: double-click `scripts\run_backtest.bat`.
* Or in PyCharm: **Run → Edit Configurations → + → Python**
  * Module name (use module, not script): `quant_scalper.cli`
  * Parameters: `backtest --top 26 --months 12 --report data/reports/run.html --csv data/reports/run.csv`
  * Working directory: project root
  * Python interpreter: your `.venv` interpreter
  * Click **Run**.

The first run downloads ≈ 50 MB of klines from
`https://data.binance.vision` and caches them under `data\cache\`.
Subsequent backtests reuse the cache and finish in seconds.

When done, open `data\reports\run.html` in your browser. You should see a
KPI summary similar to the one in the project README (≈ 79 % win-rate,
342 trades).

---

## 6. Dry-run the live bot

```powershell
.\.venv\Scripts\activate
python -m quant_scalper.cli live --dry-run
```

Or double-click `scripts\run_live_dry.bat`.

The bot scans the symbol universe every 30 seconds and **logs** what it would
do without sending any orders. Use this to see signal frequency and behaviour
before flipping to real.

---

## 7. Go to testnet (real-but-fake orders)

1. Confirm `.env` has `QS_MODE=testnet` and **valid testnet keys**.
2. Run:
   ```powershell
   .\.venv\Scripts\activate
   python -m quant_scalper.cli live --real
   ```
3. The bot will place real orders on Binance testnet. Trades are journaled to
   `data\quant_scalper.db` (open with any SQLite viewer, e.g. *DB Browser for
   SQLite*). PnL events are logged and (if configured) pushed to Telegram.

---

## 8. Going live (only after a few weeks of testnet trading look right)

* Set `QS_MODE=live` in `.env`.
* Replace the API keys with **mainnet** keys (create them at
  <https://www.binance.com/en/my/settings/api-management>; restrict
  permissions to "Enable Futures" only, no withdrawal).
* Run `python -m quant_scalper.cli live --real`. The CLI will ask you to
  type *yes* to confirm before starting in live mode.
* Start with the smallest leverage / margin you can stomach.
  The defaults (`QS_MAJOR_MARGIN_USDT=20`, `QS_ALT_MARGIN_USDT=10`) are
  intentionally tiny for the first month.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python: command not found` in PowerShell | Re-install Python and tick "Add to PATH"; close & reopen PowerShell. |
| `pip` errors mentioning `pyarrow` build failures | Make sure you're on Python 3.10/3.11/3.12. `pyarrow` doesn't ship pre-built wheels for very new (3.13+) or very old (≤3.9) interpreters yet. |
| `ImportError: parquet engine` | `pip install pyarrow` in the active venv. |
| Backtest first run hangs on download | Check your network; `data.binance.vision` must be reachable. The whole cache is ~50 MB. |
| Live runner errors `HTTP 451` / "restricted location" | Your IP is geo-blocked from Binance Futures. Either run the bot from a network that can reach `fapi.binance.com`, or set `QS_PROXY_URL` in `.env` to a local Clash / V2Ray / sing-box SOCKS or HTTP proxy (e.g. `socks5h://127.0.0.1:1080` or `http://127.0.0.1:7890`). **The historical data path (`data.binance.vision`) is not blocked**; only the live trading API is. |
| PyCharm shows red squiggles under imports despite tests passing | **File → Invalidate Caches → Invalidate and Restart**. |

---

## What lives where

```
quant-scalper\
├── .env.example            # copy to .env and fill in keys
├── README.md               # overview + backtest results
├── requirements.txt        # runtime deps (pip install -r ...)
├── requirements-dev.txt    # plus pytest / ruff / mypy
├── pyproject.toml          # editable-install + tool config
├── docs\
│   └── WINDOWS_PYCHARM.md  # this file
├── scripts\
│   ├── setup.bat           # one-click install + tests
│   ├── run_backtest.bat
│   ├── run_live_dry.bat
│   ├── run_live_real.bat
│   └── fetch_history.bat
├── pine\                   # original Pine sources (reference only)
├── data\
│   ├── cache\              # parquet klines (auto-populated, gitignored)
│   └── reports\            # HTML/CSV backtest output
├── src\quant_scalper\      # all Python source
└── tests\                  # pytest unit tests
```

Happy trading — but please test on testnet for at least 2-4 weeks before
risking real money.
