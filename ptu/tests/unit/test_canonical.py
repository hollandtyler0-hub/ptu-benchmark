from ptu.common.canonical import canonical_json, sha256_hex


def test_canonical_json_and_digest():
    assert canonical_json({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'
    assert canonical_json({"é": "✓"}) == '{"é":"✓"}'.encode()
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})
