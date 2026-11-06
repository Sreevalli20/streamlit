"""
core/order_struct.py  -  Phase 1: Binary Layout Definition
==========================================================
Rigid 21-byte wire format for all orders using Python struct.

Wire layout (format string =QdIb, native byte order, no padding):
  Bytes  0- 7   order_id   Q  uint64
  Bytes  8-15   price      d  float64 (IEEE-754)
  Bytes 16-19   quantity   I  uint32
  Byte  20      side       b  int8  (+1=BUY, -1=SELL)
"""

import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from numbers import Real

from core.constants import ORDER_FORMAT, SIDE_BUY, SIDE_SELL
from core.constants import ORDER_SIZE as _OS

ORDER_SIZE = _OS

_ORDER_STRUCT = struct.Struct(ORDER_FORMAT)
assert _ORDER_STRUCT.size == ORDER_SIZE, "struct size mismatch"


class Side(IntEnum):
    BUY  =  1
    SELL = -1


@dataclass(slots=True)
class Order:
    """Pure-Python mirror of the on-wire binary order record."""
    order_id : int
    price    : float
    quantity : int
    side     : int   # SIDE_BUY or SIDE_SELL

    def __post_init__(self):
        if isinstance(self.order_id, bool) or not isinstance(self.order_id, int):
            raise TypeError("order_id must be an integer")
        if not 0 <= self.order_id <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("order_id must fit in an unsigned 64-bit field")
        if isinstance(self.price, bool) or not isinstance(self.price, Real):
            raise TypeError("price must be a real number")
        self.price = float(self.price)
        if not math.isfinite(self.price) or self.price <= 0:
            raise ValueError("price must be finite and positive")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError("quantity must be an integer")
        if not 1 <= self.quantity <= 0xFFFFFFFF:
            raise ValueError("quantity must fit in a positive unsigned 32-bit field")
        if isinstance(self.side, bool) or not isinstance(self.side, int):
            raise TypeError("side must be an integer")
        if self.side not in (SIDE_BUY, SIDE_SELL):
            raise ValueError(f"side must be +1 or -1, got {self.side}")

    @property
    def is_buy(self) -> bool:
        return self.side == SIDE_BUY

    @property
    def is_sell(self) -> bool:
        return self.side == SIDE_SELL

    def side_str(self) -> str:
        return "BUY" if self.is_buy else "SELL"


# ---- Serialisation helpers --------------------------------------------------

def pack_order(order: "Order") -> bytes:
    """Pack one Order into exactly ORDER_SIZE raw bytes."""
    _validate_order(order)
    return _ORDER_STRUCT.pack(order.order_id, order.price,
                              order.quantity, order.side)


def unpack_order(data: bytes) -> "Order":
    """Unpack ORDER_SIZE raw bytes into an Order."""
    if len(data) != ORDER_SIZE:
        raise ValueError(f"order data must be exactly {ORDER_SIZE} bytes")
    try:
        oid, price, qty, side = _ORDER_STRUCT.unpack(data)
    except struct.error as exc:
        raise ValueError("malformed binary order data") from exc
    return Order(order_id=oid, price=price, quantity=qty, side=side)


def pack_into_buffer(buf, offset: int, order: "Order") -> None:
    """Zero-copy pack directly into an existing mutable buffer."""
    _validate_order(order)
    _ORDER_STRUCT.pack_into(buf, offset,
                            order.order_id, order.price,
                            order.quantity, order.side)


def unpack_from_buffer(buf, offset: int) -> "Order":
    """Zero-copy unpack from an existing buffer at offset."""
    try:
        oid, price, qty, side = _ORDER_STRUCT.unpack_from(buf, offset)
    except struct.error as exc:
        raise ValueError("malformed binary order data") from exc
    return Order(order_id=oid, price=price, quantity=qty, side=side)


def _validate_order(order: "Order") -> None:
    if not isinstance(order, Order):
        raise TypeError("order must be an Order instance")
    order.__post_init__()


# ---- Self-test --------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for side, label in [(SIDE_BUY, "BUY"), (SIDE_SELL, "SELL")]:
        o   = Order(order_id=1, price=100.0, quantity=500, side=side)
        raw = pack_order(o)
        o2  = unpack_order(raw)
        assert o == o2, f"round-trip failed for {label}"
        print(f"  {label}  hex={raw.hex()}  ORDER_SIZE={ORDER_SIZE}  OK")
    print("struct self-test passed")
