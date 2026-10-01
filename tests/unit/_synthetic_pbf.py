"""Minimal synthetic ``.osm.pbf`` writer for tests (no osmium, no real OSM extract).

Hand-encodes the OSM PBF container: ``OSMHeader``, then one ``OSMData`` block of
``DenseNodes`` and one of ``Way`` records (one primitive group per block, like real
extracts; older DuckDB spatial readers skip a second group). Only what ``ST_ReadOSM``
needs is emitted.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1  # two's-complement for negative int64
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _zigzag(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def _field_varint(num: int, value: int) -> bytes:
    return _varint(num << 3) + _varint(value)


def _field_bytes(num: int, payload: bytes) -> bytes:
    return _varint(num << 3 | 2) + _varint(len(payload)) + payload


def _packed_sint64_delta(values: Sequence[int]) -> bytes:
    out, prev = bytearray(), 0
    for v in values:
        out += _varint(_zigzag(v - prev))
        prev = v
    return bytes(out)


def _blob(kind: str, payload: bytes) -> bytes:
    blob = _field_varint(2, len(payload)) + _field_bytes(3, zlib.compress(payload))
    header = _field_bytes(1, kind.encode()) + _field_varint(3, len(blob))
    return struct.pack(">I", len(header)) + header + blob


def _node_block(nodes: Mapping[int, tuple[float, float]]) -> bytes:
    ids = sorted(nodes)
    to_nano = lambda deg: round(deg * 1e7)  # noqa: E731 - granularity 100 nm
    dense = (
        _field_bytes(1, _packed_sint64_delta(ids))
        + _field_bytes(8, _packed_sint64_delta([to_nano(nodes[i][1]) for i in ids]))
        + _field_bytes(9, _packed_sint64_delta([to_nano(nodes[i][0]) for i in ids]))
    )
    return _field_bytes(1, _field_bytes(1, b"")) + _field_bytes(2, _field_bytes(2, dense))


def write_pbf(
    path: Path,
    nodes: Mapping[int, tuple[float, float]],
    ways: Sequence[tuple[int, Sequence[int], Mapping[str, str]]],
    *,
    extra_node_blocks: Sequence[Mapping[int, tuple[float, float]]] = (),
) -> Path:
    """Write ``nodes`` ``{id: (lon, lat)}`` and ``ways`` ``[(id, node_ids, tags)]``.

    Ways are written in the given order. ``extra_node_blocks`` appends more node
    blocks, e.g. to repeat node ids like a non-deduplicated merge of extracts.
    """
    node_blocks = [_node_block(block) for block in (nodes, *extra_node_blocks)]

    strings = [b""]  # index 0 is the mandatory empty delimiter

    def intern(text: str) -> int:
        raw = text.encode()
        if raw not in strings:
            strings.append(raw)
        return strings.index(raw)

    way_group = b""
    for way_id, refs, tags in ways:
        keys = b"".join(_varint(intern(k)) for k in tags)
        vals = b"".join(_varint(intern(v)) for v in tags.values())
        way = (
            _field_varint(1, way_id)
            + _field_bytes(2, keys)
            + _field_bytes(3, vals)
            + _field_bytes(8, _packed_sint64_delta(list(refs)))
        )
        way_group += _field_bytes(3, way)

    table = b"".join(_field_bytes(1, s) for s in strings)
    way_block = _field_bytes(1, table) + _field_bytes(2, way_group)
    header_block = _field_bytes(4, b"OsmSchema-V0.6") + _field_bytes(4, b"DenseNodes")
    path.write_bytes(
        _blob("OSMHeader", header_block)
        + b"".join(_blob("OSMData", block) for block in node_blocks)
        + _blob("OSMData", way_block)
    )
    return path
