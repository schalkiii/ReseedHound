import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils._bencode import bdecode, bencode  # noqa: E402


def _make_sample_torrent() -> bytes:
    return bencode({
        b"announce": b"http://example.com/announce",
        b"info": {
            b"length": 1234,
            b"name": b"test.dat",
            b"piece length": 65536,
            b"pieces": b"\x00" * 20,
        },
    })


SAMPLE_TORRENT = _make_sample_torrent()


def test_bencode_roundtrip():
    decoded = bdecode(SAMPLE_TORRENT)
    encoded = bencode(decoded)
    assert encoded == SAMPLE_TORRENT, "Bencode roundtrip failed"
    print("PASS: bencode roundtrip")


def test_bencode_types():
    assert bdecode(b"i42e") == 42
    assert bdecode(b"li1ei2ei3ee") == [1, 2, 3]
    assert bdecode(b"d3:foo3:bare") == {b"foo": b"bar"}
    assert bdecode(b"4:spam") == b"spam"
    print("PASS: bencode types")


def test_info_hash():
    from src.utils.hash import compute_info_hash, sha1_hex

    decoded = bdecode(SAMPLE_TORRENT)
    info = decoded[b"info"]
    ih = compute_info_hash(info)
    info_hash_hex = sha1_hex(bencode(info))
    assert ih == info_hash_hex
    assert len(ih) == 40
    print("PASS: info_hash: %s" % ih)


def test_pieces_hash():
    from src.utils.hash import compute_pieces_hash, sha1_hex

    decoded = bdecode(SAMPLE_TORRENT)
    pieces = decoded[b"info"][b"pieces"]
    ph = compute_pieces_hash(pieces)
    pieces_hash_hex = sha1_hex(pieces)
    assert ph == pieces_hash_hex
    assert len(ph) == 40
    print("PASS: pieces_hash: %s" % ph)


def test_parser_with_temp_file():
    from src.engine.parser import TorrentParser

    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as f:
        f.write(SAMPLE_TORRENT)
        temp_path = f.name

    try:
        result = TorrentParser.parse_file(Path(temp_path))
        assert result is not None
        assert len(result["info_hash"]) == 40
        assert len(result["pieces_hash"]) == 40
        assert result["file_name"] == Path(temp_path).name
        print(f"PASS: parser info_hash={result['info_hash']}")
        print(f"PASS: parser pieces_hash={result['pieces_hash']}")
    finally:
        os.unlink(temp_path)


def test_config_loading():
    from src.storage.config import Config

    config = Config("config.yaml", "sites.yaml")
    assert config.global_config["concurrency"] == 20
    assert config.db_path == "data/cache.db"
    sites = config.sites
    print(f"PASS: config loaded, {len(sites)} sites enabled")


if __name__ == "__main__":
    test_bencode_roundtrip()
    test_bencode_types()
    test_info_hash()
    test_pieces_hash()
    test_parser_with_temp_file()
    test_config_loading()
    print()
    print("=" * 50)
    print("  所有核心模块测试通过!")
    print("=" * 50)
