import math
import struct

import pytest

from core.constants import ORDER_SIZE, SIDE_BUY, SIDE_SELL
from core.order_struct import (
    Order,
    pack_into_buffer,
    pack_order,
    unpack_from_buffer,
    unpack_order,
)


@pytest.mark.parametrize("side", [SIDE_BUY, SIDE_SELL])
def test_order_binary_round_trip(side):
    order = Order(0xFFFFFFFFFFFFFFFF, 123.456, 0xFFFFFFFF, side)
    encoded = pack_order(order)

    assert len(encoded) == ORDER_SIZE == 21
    assert unpack_order(encoded) == order
    buffer = bytearray(ORDER_SIZE + 4)
    pack_into_buffer(buffer, 2, order)
    assert unpack_from_buffer(buffer, 2) == order


@pytest.mark.parametrize(
    "values, error",
    [
        ((-1, 1.0, 1, SIDE_BUY), ValueError),
        ((1 << 64, 1.0, 1, SIDE_BUY), ValueError),
        ((True, 1.0, 1, SIDE_BUY), TypeError),
        ((1, 0.0, 1, SIDE_BUY), ValueError),
        ((1, math.inf, 1, SIDE_BUY), ValueError),
        ((1, math.nan, 1, SIDE_BUY), ValueError),
        ((1, 1.0, 0, SIDE_BUY), ValueError),
        ((1, 1.0, 1 << 32, SIDE_BUY), ValueError),
        ((1, 1.0, True, SIDE_BUY), TypeError),
        ((1, 1.0, 1, 0), ValueError),
    ],
)
def test_order_rejects_invalid_fields(values, error):
    with pytest.raises(error):
        Order(*values)


@pytest.mark.parametrize("data", [b"", bytes(ORDER_SIZE - 1), bytes(ORDER_SIZE + 1)])
def test_unpack_rejects_wrong_size(data):
    with pytest.raises(ValueError):
        unpack_order(data)


def test_unpack_rejects_invalid_wire_values():
    malformed_side = struct.pack("=QdIb", 1, 100.0, 1, 0)
    with pytest.raises(ValueError):
        unpack_order(malformed_side)


def test_pack_revalidates_mutated_order():
    order = Order(1, 100.0, 1, SIDE_BUY)
    order.quantity = 0

    with pytest.raises(ValueError):
        pack_order(order)
