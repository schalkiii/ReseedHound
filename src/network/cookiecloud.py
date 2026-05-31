import asyncio
import base64
import hashlib
import json
import logging
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

logger = logging.getLogger("seedhound")


class CookieCloudClient:
    def __init__(self, base_url: str, uuid: str, password: str):
        self._base_url = base_url.rstrip("/")
        self._uuid = uuid
        self._password = password
        self._crypto_key = (
            hashlib.md5(f"{uuid}-{password}".encode()).hexdigest()[:16].encode()
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": "SeedHound/2.0"},
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def close(self):
        if self._session:
            await self._session.close()

    def _decrypt(self, encrypted_b64: str) -> dict:
        raw = base64.b64decode(encrypted_b64)
        if raw[:8] != b"Salted__":
            raise ValueError("不支持的加密格式")
        salt = raw[8:16]
        ciphertext = raw[16:]

        key_iv = b""
        prev = b""
        while len(key_iv) < 48:
            prev = hashlib.md5(prev + self._crypto_key + salt).digest()
            key_iv += prev
        aes_key = key_iv[:32]
        aes_iv = key_iv[32:48]

        cipher = AES.new(aes_key, AES.MODE_CBC, aes_iv)
        plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
        return json.loads(plaintext.decode("utf-8"))

    async def fetch_cookies(self) -> dict[str, list[dict]]:
        url = f"{self._base_url}/get/{self._uuid}"
        params = {"password": self._password}
        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.error("CookieCloud 请求失败: HTTP %d", resp.status)
                    return {}
                data = await resp.json()
                encrypted = data.get("encrypted", "")
                if not encrypted:
                    logger.error("CookieCloud 返回数据中没有 encrypted 字段")
                    return {}
                decrypted = self._decrypt(encrypted)
                cookies_by_domain: dict[str, list[dict]] = {}
                cookie_data = decrypted.get("cookie_data", {})
                for domain, cookies in cookie_data.items():
                    if not isinstance(cookies, list):
                        continue
                    clean_domain = domain.lstrip(".")
                    if clean_domain:
                        cookies_by_domain[clean_domain] = cookies
                return cookies_by_domain
        except asyncio.TimeoutError:
            logger.error("CookieCloud 请求超时: %s", url)
            return {}
        except aiohttp.ClientError as e:
            logger.error("CookieCloud 请求错误: %s", e)
            return {}
        except Exception as e:
            logger.error("CookieCloud 解密失败: %s", e)
            return {}

    @staticmethod
    def cookies_to_string(cookies: list[dict]) -> str:
        return "; ".join(
            f"{c['name']}={c['value']}"
            for c in cookies
            if c.get("name") and c.get("value")
        )

    @staticmethod
    def match_domain(site_url: str, cookie_domains: set[str]) -> Optional[str]:
        if not site_url:
            return None
        host = urlparse(site_url).hostname or ""
        parts = host.split(".")
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in cookie_domains:
                return candidate
        if host in cookie_domains:
            return host
        for cdomain in cookie_domains:
            if host.endswith("." + cdomain):
                return cdomain
        return None
