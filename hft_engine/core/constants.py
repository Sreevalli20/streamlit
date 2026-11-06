"""core/constants.py - Central configuration for the HFT engine."""

# ---- Binary layout ----------------------------------------------------------
ORDER_FORMAT   = "=QdIb"   # native byte-order, no padding
ORDER_SIZE     = 21        # bytes per serialised order

# ---- Order sides ------------------------------------------------------------
SIDE_BUY  =  1
SIDE_SELL = -1

# ---- Ring buffer ------------------------------------------------------------
RING_BUFFER_CAPACITY = 131_072   # maximum in-flight orders
RING_HEADER_SIZE     = 16        # 8 bytes HEAD + 8 bytes TAIL

# ---- Firehose ---------------------------------------------------------------
DEFAULT_TARGET_OPS = 100_000     # orders/sec
DEFAULT_DURATION   = 30.0        # seconds

# ---- Dashboard --------------------------------------------------------------
DASHBOARD_REFRESH_MS = 50        # UI refresh interval
BOOK_LEVELS          = 8         # order book depth to display

# ---- Matching engine --------------------------------------------------------
LATENCY_WINDOW = 10_000          # rolling latency sample window

# ---- Logging ----------------------------------------------------------------
LOG_DIR = "logs"
