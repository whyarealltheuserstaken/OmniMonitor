"""GetRemainingResets protobuf fixtures. Token ids never leave the parser."""

from omnimonitor.grok_resets import fetch_remaining_resets, parse_remaining_resets


def _varint(n):
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _bytes_field(number, payload):
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def _token(token_id, expires):
    inner = _bytes_field(10, token_id.encode()) + _bytes_field(30, _varint(8) + _varint(expires))
    # field 1 of a timestamp is wire type 0, tag byte 0x08, which _varint(8) is.
    return _bytes_field(10, inner)


def _frame(payload):
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


def test_counts_unexpired_resets_and_drops_token_ids():
    now = 1_800_000_000
    payload = _token("reset-secret-id", 1_900_000_000) + _token("other-secret", 1_700_000_100)
    found = parse_remaining_resets(_frame(payload), now)
    assert found == [1_900_000_000]
    assert "reset-secret-id" not in repr(found)


def test_empty_frame_is_zero_and_garbage_is_not():
    assert parse_remaining_resets(b"\x00\x00\x00\x00\x00", 1_800_000_000) == []
    try:
        parse_remaining_resets(b"this is not protobuf", 1_800_000_000)
    except Exception as e:  # noqa: BLE001
        assert "this is not" not in str(e)
    else:
        raise AssertionError("garbage should not parse")


def test_fetch_returns_count_only():
    now = 1_800_000_000
    body = _frame(_token("reset-secret-id", 1_900_000_000))

    def transport(url, payload, timeout):
        assert url.endswith("GetRemainingResets")
        assert payload == b"\x00\x00\x00\x00\x00"
        assert timeout == 2
        return 200, body

    info = fetch_remaining_resets("bearer-secret", timeout=2, now=now, transport=transport)
    assert info["count"] == 1
    assert info["next_expires_at"] == 1_900_000_000
    blob = repr(info)
    assert "reset-secret-id" not in blob and "bearer-secret" not in blob


def test_http_error_is_not_a_zero():
    def transport(url, payload, timeout):
        return 401, b""

    info = fetch_remaining_resets("bearer-secret", transport=transport)
    assert info["count"] is None
    assert "HTTP 401" in info["reason"]
    assert "bearer-secret" not in info["reason"]
