import multiprocessing
import time

import pytest

from core.constants import SIDE_BUY
from core.order_struct import Order
from core.ring_buffer import DATA_OFF, RingBuffer


def _process_writer(path, count, ready):
    with RingBuffer(path, capacity=8, create=False) as rb:
        ready.set()
        for order_id in range(count):
            order = Order(order_id, 100.0, 1, SIDE_BUY)
            while not rb.put(order):
                time.sleep(0.0001)


def test_fifo_full_empty_and_wraparound(tmp_path):
    path = tmp_path / "orders.rb"
    with RingBuffer(path, capacity=3) as rb:
        assert rb.is_empty()
        assert rb.get() is None
        assert [rb.put(Order(i, 10.0 + i, i + 1, SIDE_BUY)) for i in range(3)] == [
            True,
            True,
            True,
        ]
        assert rb.is_full()
        assert rb.put(Order(3, 10.0, 1, SIDE_BUY)) is False
        assert rb.get().order_id == 0
        assert rb.get().order_id == 1
        assert rb.put(Order(3, 10.0, 1, SIDE_BUY))
        assert rb.put(Order(4, 10.0, 1, SIDE_BUY))
        assert rb.is_full()
        assert [rb.get().order_id for _ in range(3)] == [2, 3, 4]
        assert rb.is_empty()


def test_capacity_and_existing_file_validation(tmp_path):
    path = tmp_path / "orders.rb"
    for capacity, error in [(0, ValueError), (-1, ValueError), (True, TypeError)]:
        with pytest.raises(error):
            RingBuffer(path, capacity=capacity)
    RingBuffer(path, capacity=2).close()
    with pytest.raises(ValueError, match="file size"):
        RingBuffer(path, capacity=3, create=False)


def test_malformed_slot_does_not_consume_entry(tmp_path):
    path = tmp_path / "orders.rb"
    rb = RingBuffer(path, capacity=2)
    try:
        assert rb.put(Order(1, 100.0, 1, SIDE_BUY))
        rb._mm[DATA_OFF + 20] = 0
        with pytest.raises(ValueError, match="side"):
            rb.get()
        assert rb.size() == 1
    finally:
        rb.close()


def test_corrupt_counters_are_reported_and_close_is_idempotent(tmp_path):
    path = tmp_path / "orders.rb"
    rb = RingBuffer(path, capacity=2)
    rb._write_head(3)
    with pytest.raises(BufferError, match="invalid ring counters"):
        rb.size()
    rb._write_head(0)
    rb.close()
    rb.close()
    with pytest.raises(ValueError, match="closed"):
        rb.get()


def test_process_lock_protects_shared_ring_buffer(tmp_path):
    path = tmp_path / "shared.rb"
    count = 100
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    writer = ctx.Process(target=_process_writer, args=(str(path), count, ready))
    received = []

    with RingBuffer(path, capacity=8) as rb:
        writer.start()
        assert ready.wait(5)
        deadline = time.monotonic() + 10
        while len(received) < count and time.monotonic() < deadline:
            order = rb.get()
            if order is None:
                time.sleep(0.0001)
            else:
                received.append(order.order_id)
        writer.join(5)
        assert writer.exitcode == 0

    assert received == list(range(count))
