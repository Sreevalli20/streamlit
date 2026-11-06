# ChronosMatch - Zero-Copy High-Frequency Trading Engine

ChronosMatch is an educational Python project that demonstrates a simulated high-frequency order-processing pipeline and a local paper-trading workspace. It combines binary order records, an `mmap`-backed ring buffer, a price-time-priority limit order book, asynchronous order generation, local performance measurements, and terminal and browser dashboards.

> **Simulation only.** This project does not connect to a stock exchange or broker and does not provide live market data. Company prices, volume, liquidity, and charts in the browser workspace are synthetic. Paper orders are local simulations and are not real financial transactions.

## Features

- **Binary Order Serialization**: Fixed-width BUY/SELL order serialization using Python's `struct` module (21 bytes per order)
- **Memory-Mapped Ring Buffer**: Fixed-capacity `mmap` ring buffer with FIFO ordering, wraparound, and full/empty handling
- **SPSC Synchronization**: Single-producer/single-consumer synchronization for cooperating processes (Windows named mutexes and POSIX `flock`) plus local thread synchronization
- **Limit Order Book**: Price-time-priority limit order book with best-price matching, FIFO at the same price, partial fills, resting orders, and cancellation
- **Async Order Generation**: Asynchronous synthetic order generation and producer/consumer demos
- **Performance Metrics**: Latency, throughput, order, trade, and volume measurements
- **Dashboards**: ANSI terminal dashboard and loopback-only browser dashboard
- **Browser UI**: Markets, Stocks, Watchlist, Paper Trading, Orders, Portfolio, Analytics, System Performance, and Settings sections
- **Simulated Instruments**: RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, SBIN, ITC, LT, BHARTIARTL, and TATAMOTORS
- **Paper Trading**: Per-instrument simulated paper books, market and limit paper orders, cancellation, cash and holdings, execution history, and session charts

## Requirements

- Python **3.14.6** (tested on Windows 11)
- PowerShell or another terminal
- Runtime dependencies:
  - `sortedcontainers==2.4.0`
- Development dependencies:
  - `pytest==9.1.1`

The experimental Cython source under `hft_engine/cython_src` is optional and is not used by the regular Python CLI. It has not been built or validated.

## Installation on Windows

Run these commands from the repository root (the directory containing this README):

```powershell
# Check Python version (should be 3.14.6)
python --version

# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install -r .\hft_engine\requirements-dev.txt

# Navigate to hft_engine directory
Set-Location .\hft_engine
```

If PowerShell activation is restricted by execution policy, use the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install -r .\hft_engine\requirements-dev.txt
Set-Location .\hft_engine
```

Then use `..\.venv\Scripts\python.exe` instead of `python` in the commands below.

## Running the Application

### Browser Dashboard

From the `hft_engine` directory:

```powershell
python main.py web --host 127.0.0.1 --port 8767 --target-ops 10000
```

Open [http://127.0.0.1:8767/](http://127.0.0.1:8767/) in a browser. Press `Ctrl+C` in the server terminal to stop it. The server intentionally binds to a loopback address and is not a public web server.

### Terminal Dashboard

```powershell
python main.py dashboard --duration 5 --target-ops 50000
```

### Integrated Demo

Run a short integrated producer-buffer-consumer demonstration:

```powershell
python scripts/run_demo.py --duration 2 --target-ops 50000
```

All target rates are requested generation rates, not guaranteed throughput. Reported values are measurements from that run and vary by machine, workload, and system load.

## Testing and Verification

Run from the `hft_engine` directory:

```powershell
# Binary serialization and ring-buffer smoke checks
python main.py struct-test
python main.py ring-test

# Matching-engine correctness checks and a local latency sample
python main.py verify

# Complete automated test suite
python -m pytest -q

# Python syntax/bytecode compilation
python -m compileall -q .

# Optional measured serialization/transport comparison
python main.py audit --orders 10000
```

The tests cover serialization validation, ring-buffer behavior, matching correctness, paper-trading behavior, HTTP API workflows, and clean shutdown. The latest recorded test run completed with **65 passed** on Python 3.14.6.

Benchmark results are measurements, not promises or production performance claims. Serialization and decoding create Python objects, so the complete application is not end-to-end zero-copy.

## Useful CLI Commands

```powershell
# Show all available commands
python main.py --help

# Run standalone firehose (no consumer)
python main.py firehose --duration 5 --target-ops 100000

# Run transport/serialization benchmark
python main.py audit --orders 10000

# Separate producer/consumer commands
python scripts/run_producer.py --help
python scripts/run_consumer.py --help
```

The standalone firehose has no consumer; its bounded buffer can fill and count dropped orders. The integrated demo and web application run producer and consumer workflows together.

## Git Commands

### Repository Information

- **Current Branch**: `main`
- **Remote Repository**: `https://github.com/Sreevalli20/infotac1.git`

### Committing Changes

To commit the new README and keep other existing local changes out of that commit:

```powershell
# Check current status
git status

# Stage only the README file
git add README.md

# Review staged changes
git diff --cached -- README.md

# Commit the staged changes
git commit -m "Add ChronosMatch project README"

# Review status before pushing
git status
```

### Pushing to Remote

When ready to publish the commit to GitHub:

```powershell
git push origin main
```

### Important Notes

- Review `git status` before staging other files
- Do not use `git add .` unless you intend to include every modified, deleted, and untracked file in the worktree
- If you manage the configured remote yourself, ensure you have the proper permissions before pushing

## Project Structure

```
hft_engine/
  bench/           Matching verification and transport audit
  core/            Order format, ring buffer, firehose, metrics, paper market
  engine/          Price-time-priority limit order book
  scripts/         Integrated demo and separate producer/consumer commands
  tests/           Pytest regression and integration tests
  ui/              Terminal dashboard, browser UI, and local HTTP server
  cython_src/      Optional experimental Cython source
  main.py          Unified command-line entry point
  requirements.txt         Runtime dependencies
  requirements-dev.txt     Development dependencies
  setup.py                Package setup configuration
```

## Architecture Details

### Order Record
- 21 bytes using `=QdIb` format
- unsigned 64-bit ID
- float64 price
- unsigned 32-bit quantity
- signed 8-bit side (BUY/SELL)
- Native byte order with standard field sizes and no padding

### Ring Buffer
- 16-byte header and fixed-capacity circular data region
- Non-blocking API: `put` returns `False` when full, `get` returns `None` when empty
- Protected by `threading.Lock` for local threads
- Windows: named OS mutex derived from canonical file path
- POSIX: uses `flock`
- Intended topology: one producer and one consumer

### Matching Engine
- Limit orders match at the resting order's price
- Best price first, FIFO within a price
- Preserves residual quantities
- Partial fills supported

### Measurement
- Uses monotonic/performance clocks for elapsed times
- Latency and throughput values are observations, not service-level guarantees

## Troubleshooting

- **Import failures**: Confirm the terminal is in `hft_engine`, activate the environment, and run `python -m pip install -r requirements-dev.txt`
- **Wrong Python version**: Select Python 3.14.6 in VS Code and recreate the virtual environment with that interpreter
- **IPC file leftover**: Stop both processes and pass the same path to the next producer and consumer. Starting the producer with `create=True` truncates the existing file
- **Consumer timeout**: The separate consumer exits after its configured idle timeout (60 seconds by default); use `python scripts/run_consumer.py --help` to adjust it
- **KeyboardInterrupt**: In the integrated demo/dashboard, this requests producer stop, drains queued orders, joins worker threads, and closes the mapped file

## Disclaimer

This is an educational local simulation, not a production trading system or investment tool. The browser market data does not include real NSE/BSE quotes, NIFTY 50 or SENSEX values, historical exchange data, or exchange market depth. Its chart ranges display available observations from the current server session, not historical market records.
