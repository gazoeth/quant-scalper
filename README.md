# quant-scalper

A 15-minute scalping bot for **Binance USDT-M perpetual futures** that combines:

* 缠论 (Chan Lun) — fractals, strokes (笔), pivots (中枢), MACD-divergence (背驰)
* Breaker Blocks (LuxAlgo)
* Bollinger Bands + RSI mean-reversion triggers
* EMA20/EMA50 trend gating
* 1-hour MTF (multi-timeframe) trend filter
* ATR-based dynamic stops + trailing exit

The strategy fires only when **all** of the above filters confirm. The result is
low frequency, high quality entries.

## Backtest result (default config)

26 USDT-M perpetuals × 12 months of 15-minute candles, real Binance data sourced
from `data.binance.vision`:

| metric | value |
|---|---|
| trades | **342** |
| **win-rate** | **79.5 %** |
| expectancy | +0.25 USDT/trade |
| profit factor | 1.96 |
| total PnL | +84.91 USDT |
| max drawdown | 2.9 % |
| symbols ≥ 75 % WR (n≥10) | 17 / 22 |
| profitable months | 10 / 12 |

> **Caveat.** Average winner is ~$0.64, average loser ~$1.27 (R:R ≈ 1:2). The
> positive expectancy is carried by the high win-rate. If realised win-rate
> drops below ~67 % the strategy turns net-negative — so test on Binance testnet
> for a few weeks before any live capital.

Run it yourself:

```bash
python -m quant_scalper.cli backtest --top 26 --months 12 \
  --report data/reports/run.html --csv data/reports/run.csv
```

## Setup

```bash
git clone https://github.com/gazoeth/quant-scalper.git
cd quant-scalper
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
cp .env.example .env   # then fill in keys
pytest                  # smoke tests
```

`.env` reference:

```ini
QS_MODE=testnet                  # testnet | paper | live
BINANCE_API_KEY=...
BINANCE_API_SECRET=...

QS_TIMEFRAME=15m
QS_MTF_TIMEFRAME=1h

QS_MAJOR_LEVERAGE=10             # leverage for major coins
QS_ALT_LEVERAGE=3                # leverage for altcoins
QS_MAJOR_MARGIN_USDT=20          # margin (USDT) for major coins
QS_ALT_MARGIN_USDT=10            # margin (USDT) for altcoins

QS_LOG_LEVEL=INFO
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## CLI

```bash
# pre-warm cache for one symbol
python -m quant_scalper.cli fetch BTC/USDT --timeframe 15m --months 12

# full multi-symbol backtest
python -m quant_scalper.cli backtest --top 26 --months 12 \
  --report data/reports/run.html --csv data/reports/run.csv

# dry-run live (no orders sent)
python -m quant_scalper.cli live --dry-run

# real testnet/live (will prompt to confirm in live mode)
python -m quant_scalper.cli live --real
```

## Architecture

```
src/quant_scalper/
├── config.py              # pydantic settings
├── indicators/
│   ├── basic.py           # EMA / RSI / BB / ATR / MACD
│   ├── chan_lun.py        # 分型 / 笔 / 中枢 / 背驰
│   └── breaker_blocks.py  # LuxAlgo breaker pivots → signals
├── strategy/
│   ├── signal.py          # 7-way confluence aggregator
│   ├── backtest.py        # vectorised backtester (slip + fee + trail)
│   └── runner.py          # multi-symbol orchestrator + HTML report
├── exchange/
│   ├── data.py            # data.binance.vision historical loader
│   └── connector.py       # ccxt.binanceusdm REST client
├── live/
│   └── runner.py          # 15m loop: scan + open + manage + close
├── monitor/
│   └── reporter.py        # Telegram + console PnL reports
├── storage/
│   └── db.py              # SQLite trade journal
└── cli.py                 # `quant-scalper backtest|live|fetch`
```

## Geo-restriction note

Many cloud / VPS providers (incl. AWS US, GCP US, most European DCs) are blocked
from `fapi.binance.com` with HTTP 451. **Historical data** for backtesting
sidesteps this by using `data.binance.vision`'s public klines archive, but the
**live runner** needs an IP that can reach Binance Futures.

If your IP is blocked, run any local Clash / V2Ray / sing-box / Shadowsocks
client and point the bot at it via the **`QS_PROXY_URL`** setting in `.env`:

```ini
# whatever your client exposes — examples:
QS_PROXY_URL=socks5h://127.0.0.1:1080
QS_PROXY_URL=http://127.0.0.1:7890
```

The connector will route every Binance API call through that proxy. Standard
`HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` env vars are also honoured.

## Risk disclaimers

This is software for educational use. **Past backtest performance does not
guarantee future returns.** Cryptocurrency perpetual-futures trading can result
in total loss of margin and beyond. Always start on testnet, never deploy
capital you cannot afford to lose, and review every order the bot places.

## Pine sources

The original Pine Script files (`CL_AI v7.6` indicator and the `Breaker Blocks
with Signals` indicator) are kept under `pine/` for reference. See
[`pine/README.md`](pine/README.md) for what we ported and what we deliberately
did not.
