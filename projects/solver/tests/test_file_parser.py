from io import BytesIO

from backend.core.file_parser import parse_file_bytes


def test_plain_text_and_json():
    assert "A 产品" in parse_file_bytes("problem.txt", "A 产品 利润 40 元".encode("utf-8"))
    payload = parse_file_bytes("case.json", b'{"x": 1, "y": "ok"}')
    assert '"x": 1' in payload


def test_gbk_text_is_decoded():
    text = "牧场 F01 普通奶 30 吨".encode("gbk")
    assert "F01" in parse_file_bytes("notes.txt", text)


def test_csv_decoded_with_bom_or_utf8():
    csv = "name,value\nA,1\nB,2\n".encode("utf-8-sig")
    out = parse_file_bytes("table.csv", csv)
    assert "name,value" in out
    assert "A,1" in out


def test_unsupported_extension_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_file_bytes("mystery.xyz", b"hello")
