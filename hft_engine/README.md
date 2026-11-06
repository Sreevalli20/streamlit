# ChronosMatch

ChronosMatch is an educational Python prototype for a simulated order-processing
pipeline. It includes a fixed-width binary order record, an mmap-backed
single-producer/single-consumer ring buffer, a price-time-priority limit order
book, an asynchronous order generator, latency/throughput counters, and a
terminal dashboard.

It is not a live trading system. The ring buffer uses binary serialization and
memory mapping, but the consumer reconstructs Python `Order` objects; the
complete pipeline is not end-to-end zero-copy. The local browser dashboard is
served by the Python web server and is connected to its local simulated engine.
Its company prices, quotes, and session charts are synthetic, not exchange data.

## Requirements

- Python **3.14.6** (the project was tested on Windows 11 with this exact
  version).
- `pip`.
- Windows PowerShell or another terminal.

The Python matching engine requires `sortedcontainers`. Pytest is required only
for the development test suite. The optional Cython extension is not required
to run the Python engine. Cython is not installed in the tested environment, so
the extension was not built or validated with Python 3.14.6.

## Setup (PowerShell)

Run these commands from the directory containing `README.md`:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

The first command should print `Python 3.14.6`. If the `python` command selects
another interpreter, activate a Python 3.14.6 environment before proceeding.

## Run and test

```powershell
# Full automated regression suite, including a spawned-process IPC test
python -m pytest -q

# Binary record and ring-buffer smoke checks
python main.py struct-test
python main.py ring-test

# Deterministic matching checks plus an actual local latency sample
python main.py verify

# Short producer -> mmap buffer -> consumer -> matching-engine demo
python scripts/run_demo.py --duration 2 --target-ops 50000

# Optional transport/serialization benchmark; reports measured local rates
python main.py audit --orders 10000

# Optional live terminal dashboard
python main.py dashboard --duration 5 --target-ops 50000
```

The demo reports counts and timing measured during that run. The random
simulation is not seeded, and the observed rates vary with the machine and
system load. The benchmark prints its Python version, platform, workload, and
elapsed time for each case; compare runs made under similar conditions. Its
mmap, struct, pickle, and JSON paths have different allocation/serialization
costs, so the ratios are informative measurements, not a controlled proof of
zero-copy behavior or production performance.

Successful smoke checks print `PASS` messages. The integrated demo prints the
orders sent/processed, trades, volume, drops, observed average/P50/P99 latency,
and the final best bid/ask. Those values vary between runs; no fixed benchmark
number is an expected result.

## Architecture and limits

- **Order record:** 21 bytes using `=QdIb` (unsigned 64-bit ID, float64 price,
  unsigned 32-bit quantity, signed 8-bit side). The `=` prefix uses native
  byte order with standard field sizes and no padding.
- **Ring buffer:** 16-byte header and fixed-capacity circular data region.
  The API does not wait for space or data: `put` returns `False` when full;
  `get` returns `None` when empty. Calls may wait briefly to acquire a
  synchronization lock. A `threading.Lock` protects local threads. On Windows
  a named OS mutex derived from the canonical file path serializes cooperating
  processes; on POSIX the implementation uses `flock`. Both processes must use
  the same backing file and capacity. The intended topology is one producer
  and one consumer.
- **Matching:** limit orders match at the resting order's price, best price
  first, FIFO within a price, and preserve residual quantities.
- **Measurement:** monotonic/performance clocks are used for elapsed times.
  Latency and throughput values are observations, not service-level guarantees.
- **Optional Cython:** `cython_src/lob.pyx` is experimental and not used by the
  Python CLI. Its compilation and behavior have not been verified on Python
  3.14.6; no Cython speed claim is made.
- **Web page:** `ui/web_dashboard.html` is a standalone front-end mock-up with
  generated values. It does not read the Python process or report real metrics.

## Troubleshooting

- If imports fail, confirm the terminal is in `hft_engine`, activate the
  environment, and run `python -m pip install -r requirements-dev.txt`.
- If `python --version` is not 3.14.6, select the correct interpreter in VS Code
  and recreate the virtual environment with that interpreter.
- If an IPC file was left behind after an interrupted separate-process run,
  stop both processes and pass the same path to the next producer and consumer.
  Starting the producer with `create=True` truncates the existing file.
- The separate consumer exits after its configured idle timeout (60 seconds by
  default); use `python scripts/run_consumer.py --help` to adjust it.
- `KeyboardInterrupt` in the integrated demo/dashboard requests producer stop,
  drains queued orders, joins worker threads, and closes the mapped file.

For the complete project overview, accurate limitations, Git instructions, and
current run commands, see the [repository README](../README.md).
