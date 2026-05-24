import hashlib


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def compute_info_hash(info_dict: dict) -> str:
    from ._bencode import bencode
    return sha1_hex(bencode(info_dict))


def compute_pieces_hash(pieces: bytes) -> str:
    return sha1_hex(pieces)
