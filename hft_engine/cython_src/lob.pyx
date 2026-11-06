# cython_src/lob.pyx  -  Phase 2: Cython Limit Order Book
# =========================================================
# Compile:
#   python setup.py build_ext --inplace
#
# Requires: Cython + MSVC (Windows) or GCC/Clang (Linux/macOS)
# Experimental optional extension; compilation and performance are unverified.
#
# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: nonecheck=False

from libc.stdlib  cimport malloc, free, qsort
from libc.string  cimport memset, memmove
from libc.stdint  cimport uint64_t, uint32_t, int8_t
import time

DEF SIDE_BUY  =  1
DEF SIDE_SELL = -1
DEF MAX_DEPTH = 65536   # max resting orders per side

# ---- C structs --------------------------------------------------------------

cdef struct COrder:
    uint64_t order_id
    double   price
    uint32_t quantity
    int8_t   side
    uint64_t seq

cdef struct CTrade:
    uint64_t aggressor_id
    uint64_t resting_id
    double   price
    uint32_t quantity

# ---- qsort comparators ------------------------------------------------------

cdef int bid_cmp(const void* a, const void* b) noexcept nogil:
    """Bids: descending price, then ascending seq."""
    cdef const COrder* oa = <const COrder*>a
    cdef const COrder* ob = <const COrder*>b
    if ob.price > oa.price: return  1
    if ob.price < oa.price: return -1
    if oa.seq   < ob.seq:   return -1
    if oa.seq   > ob.seq:   return  1
    return 0

cdef int ask_cmp(const void* a, const void* b) noexcept nogil:
    """Asks: ascending price, then ascending seq."""
    cdef const COrder* oa = <const COrder*>a
    cdef const COrder* ob = <const COrder*>b
    if oa.price < ob.price: return -1
    if oa.price > ob.price: return  1
    if oa.seq   < ob.seq:   return -1
    if oa.seq   > ob.seq:   return  1
    return 0

# ---- helper: remove element at index from C array ---------------------------

cdef inline void _remove_at(COrder* arr, int* count, int idx) nogil:
    cdef int tail = count[0] - 1
    if idx < tail:
        memmove(&arr[idx], &arr[idx+1], (tail - idx) * sizeof(COrder))
    count[0] -= 1

# ---- CythonLOB --------------------------------------------------------------

cdef class CythonLOB:
    """
    Cython Limit Order Book.
    Experimental implementation. Returned trade tuples allocate Python objects;
    its behavior and performance have not been validated against the Python LOB.

    Public methods (callable from Python):
        submit(order_id, price, quantity, side) -> list of (agg_id, rest_id, px, qty)
        best_bid  : float | None
        best_ask  : float | None
    """
    cdef COrder*  _bids
    cdef COrder*  _asks
    cdef int      _bid_n
    cdef int      _ask_n
    cdef uint64_t _seq

    cdef public long total_orders
    cdef public long total_trades
    cdef public long total_volume
    cdef public double last_match_us

    def __cinit__(self):
        self._bids  = <COrder*>malloc(MAX_DEPTH * sizeof(COrder))
        self._asks  = <COrder*>malloc(MAX_DEPTH * sizeof(COrder))
        self._bid_n = 0
        self._ask_n = 0
        self._seq   = 0
        self.total_orders  = 0
        self.total_trades  = 0
        self.total_volume  = 0
        self.last_match_us = 0.0
        if not self._bids or not self._asks:
            raise MemoryError("CythonLOB: malloc failed")

    def __dealloc__(self):
        if self._bids: free(self._bids)
        if self._asks: free(self._asks)

    cpdef list submit(self, uint64_t order_id, double price,
                      uint32_t quantity, int8_t side):
        """Match and/or rest an order.  Returns list of trade tuples."""
        self._seq      += 1
        self.total_orders += 1

        cdef COrder entry
        entry.order_id = order_id
        entry.price    = price
        entry.quantity = quantity
        entry.side     = side
        entry.seq      = self._seq

        cdef list   trades    = []
        cdef CTrade t
        cdef COrder* best
        cdef uint32_t fill

        cdef long t0_ns = time.perf_counter_ns()

        if side == SIDE_BUY:
            qsort(self._asks, self._ask_n, sizeof(COrder), ask_cmp)
            while entry.quantity > 0 and self._ask_n > 0:
                best = &self._asks[0]
                if best.price > price:
                    break
                fill            = min(entry.quantity, best.quantity)
                t.aggressor_id  = order_id
                t.resting_id    = best.order_id
                t.price         = best.price
                t.quantity      = fill
                trades.append((t.aggressor_id, t.resting_id, t.price, t.quantity))
                entry.quantity -= fill
                best.quantity  -= fill
                if best.quantity == 0:
                    _remove_at(self._asks, &self._ask_n, 0)
            if entry.quantity > 0:
                if self._bid_n < MAX_DEPTH:
                    self._bids[self._bid_n] = entry
                    self._bid_n += 1
        else:  # SIDE_SELL
            qsort(self._bids, self._bid_n, sizeof(COrder), bid_cmp)
            while entry.quantity > 0 and self._bid_n > 0:
                best = &self._bids[0]
                if best.price < price:
                    break
                fill            = min(entry.quantity, best.quantity)
                t.aggressor_id  = order_id
                t.resting_id    = best.order_id
                t.price         = best.price
                t.quantity      = fill
                trades.append((t.aggressor_id, t.resting_id, t.price, t.quantity))
                entry.quantity -= fill
                best.quantity  -= fill
                if best.quantity == 0:
                    _remove_at(self._bids, &self._bid_n, 0)
            if entry.quantity > 0:
                if self._ask_n < MAX_DEPTH:
                    self._asks[self._ask_n] = entry
                    self._ask_n += 1

        self.last_match_us  = (time.perf_counter_ns() - t0_ns) / 1_000.0
        self.total_trades  += len(trades)
        self.total_volume  += sum(q for _,_,_,q in trades)
        return trades

    @property
    def best_bid(self):
        if self._bid_n == 0: return None
        cdef double best = self._bids[0].price
        for i in range(1, self._bid_n):
            if self._bids[i].price > best:
                best = self._bids[i].price
        return best

    @property
    def best_ask(self):
        if self._ask_n == 0: return None
        cdef double best = self._asks[0].price
        for i in range(1, self._ask_n):
            if self._asks[i].price < best:
                best = self._asks[i].price
        return best

    def stats(self):
        return {
            "total_orders"  : self.total_orders,
            "total_trades"  : self.total_trades,
            "total_volume"  : self.total_volume,
            "bid_depth"     : self._bid_n,
            "ask_depth"     : self._ask_n,
            "best_bid"      : self.best_bid,
            "best_ask"      : self.best_ask,
            "last_match_us" : self.last_match_us,
        }
