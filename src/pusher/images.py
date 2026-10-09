"""Upload selected public article images for Feishu card components."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import requests

from .. import config

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _public_https_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        if "." not in host:
            return False
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            return False
        return bool(addresses) and all(
            ipaddress.ip_address(info[4][0]).is_global for info in addresses
        )


class FeishuImageUploader:
    def __init__(self, *, session=None) -> None:
        self.session = session or requests.Session()

    def _tenant_token(self) -> str:
        response = self.session.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise ValueError("Feishu app token request failed")
        return data["tenant_access_token"]

    def upload(self, url: str, *, token: str) -> str:
        if not _public_https_url(url):
            raise ValueError("image URL must be public HTTPS")
        response = self.session.get(url, timeout=10, stream=True, allow_redirects=False)
        try:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("image redirects are not followed")
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if mime not in IMAGE_TYPES:
                raise ValueError("unsupported image content type")
            size = int(response.headers.get("Content-Length") or 0)
            if size > MAX_IMAGE_BYTES:
                raise ValueError("image is too large")
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError("image is too large")
                chunks.append(chunk)
            if not total:
                raise ValueError("image is empty")
        finally:
            response.close()
        upload = self.session.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": ("news-image", b"".join(chunks), mime)},
            timeout=15,
        )
        upload.raise_for_status()
        data = upload.json()
        key = (data.get("data") or {}).get("image_key")
        if data.get("code") != 0 or not key:
            raise ValueError("Feishu image upload failed")
        return key

    def upload_many(self, urls: list[str]) -> dict[str, str]:
        if not config.FEISHU_APP_ID or not config.FEISHU_APP_SECRET or not urls:
            return {}
        token = self._tenant_token()
        result = {}
        for url in urls:
            try:
                result[url] = self.upload(url, token=token)
            except (requests.RequestException, ValueError):
                # The Markdown retains a clickable source-image link.
                continue
        return result
