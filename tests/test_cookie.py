"""CookieCloud：解密、域名匹配、Cookie 拼装、写回 sites.yaml。"""
import base64
import hashlib
import json

import pytest
import yaml
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from src.cookie_sync import sync_cookies
from src.network.cookiecloud import CookieCloudClient


def _openssl_encrypt(plaintext: bytes, uuid: str, password: str, salt: bytes) -> str:
    """按 CookieCloud 的 OpenSSL EVP_BytesToKey 派生规则生成密文。"""
    crypto_key = hashlib.md5(f"{uuid}-{password}".encode()).hexdigest()[:16].encode()
    key_iv = b""
    prev = b""
    while len(key_iv) < 48:
        prev = hashlib.md5(prev + crypto_key + salt).digest()
        key_iv += prev
    cipher = AES.new(key_iv[:32], AES.MODE_CBC, key_iv[32:48])
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(b"Salted__" + salt + ciphertext).decode()


def test_cookiecloud_decrypt_roundtrip():
    payload = {"cookie_data": {"example.com": [{"name": "a", "value": "1"}]}}
    encrypted = _openssl_encrypt(json.dumps(payload).encode(), "uid", "pwd", b"12345678")
    client = CookieCloudClient("http://cc", "uid", "pwd")
    assert client._decrypt(encrypted) == payload


def test_cookiecloud_decrypt_rejects_unknown_format():
    client = CookieCloudClient("http://cc", "uid", "pwd")
    with pytest.raises(ValueError, match="不支持的加密格式"):
        client._decrypt(base64.b64encode(b"not salted").decode())


def test_cookies_to_string_filters_invalid_entries():
    cookies = [
        {"name": "a", "value": "1"},
        {"name": "", "value": "2"},
        {"name": "b"},
        {"value": "3"},
    ]
    assert CookieCloudClient.cookies_to_string(cookies) == "a=1"


@pytest.mark.parametrize("site_url,domains,expected", [
    ("https://tracker.example.com/announce", {"example.com"}, "example.com"),
    ("https://a.b.example.com/x", {"b.example.com", "other.com"}, "b.example.com"),
    ("https://exact.example.com", {"exact.example.com"}, "exact.example.com"),
    ("https://sub.example.com", {"example.com"}, "example.com"),
    ("https://nomatch.org", {"example.com"}, None),
    ("", {"example.com"}, None),
])
def test_match_domain(site_url, domains, expected):
    assert CookieCloudClient.match_domain(site_url, domains) == expected


@pytest.mark.asyncio
async def test_fetch_cookies_normalizes_leading_dot(monkeypatch):
    class Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def json(self):
            return {"encrypted": "dummy"}

    class Session:
        def get(self, url, params=None):
            return Resp()

    client = CookieCloudClient("http://cc", "uid", "pwd")
    client._session = Session()
    monkeypatch.setattr(CookieCloudClient, "_decrypt", lambda self, data: {
        "cookie_data": {
            ".example.com": [{"name": "a", "value": "1"}],
            "invalid": "not a list",
        },
    })
    assert await client.fetch_cookies() == {
        "example.com": [{"name": "a", "value": "1"}],
    }


def _patch_cookiecloud(monkeypatch, cookies):
    async def fake_start(self):
        self._session = None

    async def fake_fetch(self):
        return cookies

    async def fake_close(self):
        self._session = None

    monkeypatch.setattr(CookieCloudClient, "start", fake_start)
    monkeypatch.setattr(CookieCloudClient, "fetch_cookies", fake_fetch)
    monkeypatch.setattr(CookieCloudClient, "close", fake_close)


@pytest.mark.asyncio
async def test_sync_cookies_writes_back_matched_sites(config_files, monkeypatch):
    _, sites_path, _, _ = config_files
    _patch_cookiecloud(monkeypatch, {
        "a.example.com": [{"name": "sid", "value": "abc"}],
    })
    assert await sync_cookies("http://cc", "u", "p", str(sites_path)) == 0
    written = yaml.safe_load(sites_path.read_text(encoding="utf-8"))
    assert written["sites"][0]["cookie"] == "sid=abc"
    assert "cookie" not in written["sites"][1]
    assert written["sites"][0]["passkey"] == "pa"


@pytest.mark.asyncio
async def test_sync_cookies_returns_one_when_empty(config_files, monkeypatch):
    _, sites_path, _, _ = config_files
    before = sites_path.read_text(encoding="utf-8")
    _patch_cookiecloud(monkeypatch, {})
    assert await sync_cookies("http://cc", "u", "p", str(sites_path)) == 1
    assert sites_path.read_text(encoding="utf-8") == before  # 无 Cookie 时不改写文件
