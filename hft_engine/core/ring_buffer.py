"""
core/ring_buffer.py  -  Phase 1: Memory-Mapped Ring Buffer
==========================================================
Single-Producer / Single-Consumer (SPSC) circular buffer backed by an
mmap region so two OS processes can share it via a named file.

Memory layout
-------------
  Bytes  0- 7  : HEAD counter  (uint64, little-endian) - next write sequence
  Bytes  8-15  : TAIL counter  (uint64, little-endian) - next read sequence
  Bytes 16-..  : circular data region  (CAPACITY x ORDER_SIZE bytes)

Thread safety: a threading.Lock guards access within one process.
Cross-process safety: Windows uses a named OS mutex and POSIX uses flock;
all participants must open the same backing path and use the same capacity.
"""

import ctypes
import hashlib
import mmap
import os
import struct
import threading
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from core.constants import ORDER_SIZE, RING_BUFFER_CAPACITY, RING_HEADER_SIZE
from core.order_struct import Order, pack_into_buffer, unpack_from_buffer

_HEAD_OFF = 0
_TAIL_OFF = 8
_PTR_FMT  = "<Q"   # little-endian uint64
_PTR_SZ   = struct.calcsize(_PTR_FMT)
DATA_OFF  = RING_HEADER_SIZE   # 16 bytes


class RingBuffer:
    """
    Memory-mapped SPSC ring buffer.

    Parameters
    ----------
    path     : str | Path   backing file (created if create=True)
    capacity : int          maximum number of in-flight orders
    create   : bool         True  -> producer (creates/truncates the file)
                            False -> consumer (opens existing file read-write)
    """

    def __init__(self, path, capacity: int = RING_BUFFER_CAPACITY,
                 create: bool = True):
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an integer")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.path       = Path(path)
        self.capacity   = capacity
        self._data_size = capacity * ORDER_SIZE
        self._total     = DATA_OFF + self._data_size
        self._lock      = threading.Lock()
        self._kernel32  = None
        self._mutex     = None

        if create:
            self.path.write_bytes(b"\x00" * self._total)

        self._fh = open(self.path, "r+b")  # noqa: SIM115 - retained for the mmap lifetime.
        try:
            if os.name == "nt":
                self._create_process_mutex()
            if os.fstat(self._fh.fileno()).st_size != self._total:
                raise ValueError(
                    f"ring file size does not match capacity {capacity}"
                )
            self._mm = mmap.mmap(self._fh.fileno(), self._total)
            with self._guard():
                head = self._read_head()
                tail = self._read_tail()
                self._validate_counters(head, tail)
        except BaseException:
            if hasattr(self, "_mm"):
                self._mm.close()
            self._fh.close()
            self._close_process_mutex()
            raise

    def _create_process_mutex(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        self._kernel32.ReleaseMutex.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        canonical_path = os.path.normcase(str(self.path.resolve()))
        digest = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()
        self._mutex = self._kernel32.CreateMutexW(
            None, False, f"Local\\ChronosMatchRingBuffer-{digest}"
        )
        if not self._mutex:
            raise ctypes.WinError(ctypes.get_last_error())

    def _close_process_mutex(self) -> None:
        if self._mutex is not None:
            if not self._kernel32.CloseHandle(self._mutex):
                raise ctypes.WinError(ctypes.get_last_error())
            self._mutex = None

    @contextmanager
    def _guard(self):
        """Serialize threads and cooperating processes using this backing file."""
        with self._lock:
            if self._mm.closed:
                raise ValueError("ring buffer is closed")
            if os.name == "nt":
                status = self._kernel32.WaitForSingleObject(
                    self._mutex, 0xFFFFFFFF
                )
                if status not in (0, 0x80):
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    yield
                finally:
                    if not self._kernel32.ReleaseMutex(self._mutex):
                        raise ctypes.WinError(ctypes.get_last_error())
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)

    def _validate_counters(self, head: int, tail: int) -> None:
        if head < tail or head - tail > self.capacity:
            raise BufferError(
                f"invalid ring counters: head={head}, tail={tail}, "
                f"capacity={self.capacity}"
            )

    # ---- pointer helpers ----------------------------------------------------

    def _read_head(self) -> int:
        return struct.unpack_from(_PTR_FMT, self._mm, _HEAD_OFF)[0]

    def _read_tail(self) -> int:
        return struct.unpack_from(_PTR_FMT, self._mm, _TAIL_OFF)[0]

    def _write_head(self, v: int) -> None:
        struct.pack_into(_PTR_FMT, self._mm, _HEAD_OFF, v)

    def _write_tail(self, v: int) -> None:
        struct.pack_into(_PTR_FMT, self._mm, _TAIL_OFF, v)

    # ---- public API ---------------------------------------------------------

    def put(self, order: Order) -> bool:
        """Write one order.  Returns True on success, False if full (non-blocking)."""
        with self._guard():
            head = self._read_head()
            tail = self._read_tail()
            self._validate_counters(head, tail)
            if (head - tail) >= self.capacity:
                return False
            if head == 0xFFFFFFFFFFFFFFFF:
                raise OverflowError("ring write counter exhausted")
            slot   = head % self.capacity
            offset = DATA_OFF + slot * ORDER_SIZE
            pack_into_buffer(self._mm, offset, order)
            self._write_head(head + 1)
            return True

    def get(self) -> Order | None:
        """Read one order.  Returns None if empty (non-blocking)."""
        with self._guard():
            head = self._read_head()
            tail = self._read_tail()
            self._validate_counters(head, tail)
            if head == tail:
                return None
            if tail == 0xFFFFFFFFFFFFFFFF:
                raise OverflowError("ring read counter exhausted")
            slot   = tail % self.capacity
            offset = DATA_OFF + slot * ORDER_SIZE
            order  = unpack_from_buffer(self._mm, offset)
            self._write_tail(tail + 1)
            return order

    def size(self) -> int:
        with self._guard():
            head = self._read_head()
            tail = self._read_tail()
            self._validate_counters(head, tail)
            return head - tail

    def is_empty(self) -> bool:
        return self.size() == 0

    def is_full(self) -> bool:
        return self.size() >= self.capacity

    def flush(self) -> None:
        with self._lock:
            if not self._mm.closed:
                self._mm.flush()

    def close(self) -> None:
        with self._lock:
            try:
                if not self._mm.closed:
                    try:
                        self._mm.flush()
                    finally:
                        self._mm.close()
            finally:
                try:
                    if not self._fh.closed:
                        self._fh.close()
                finally:
                    self._close_process_mutex()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __repr__(self):
        return (f"RingBuffer(path={self.path.name!r}, "
                f"capacity={self.capacity:,}, size={self.size()})")


# ---- Self-test --------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    from core.constants import SIDE_BUY, SIDE_SELL

    with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as f:
        tmp = f.name
    try:
        rb = RingBuffer(tmp, capacity=8, create=True)
        print(repr(rb))
        for i in range(8):
            side = SIDE_BUY if i % 2 == 0 else SIDE_SELL
            assert rb.put(Order(order_id=i, price=100.0+i, quantity=10, side=side))
        assert rb.is_full(),  "should be full"
        print(f"  full : size={rb.size()}")
        for i in range(8):
            o = rb.get()
            assert o is not None and o.order_id == i
        assert rb.is_empty(), "should be empty"
        print(f"  empty: size={rb.size()}")
        print("OK RingBuffer self-test passed")
    finally:
        rb.close()
        os.unlink(tmp)
