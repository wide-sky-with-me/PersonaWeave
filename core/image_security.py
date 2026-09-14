"""Small, shared safety policy for externally supplied image URLs."""

import base64
import binascii
import ipaddress
import socket
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import cv2
import httpx
import numpy as np
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_REDIRECTS = 4
_ALLOWED_DATA_MIMES = {"image/jpeg", "image/png", "image/webp"}
_MANAGED_URL_ROOTS = {
    "/api/v1/data/": Path("data"),
    "/api/v1/files/": Path("outputs/api_results"),
}


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not any(
        (ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_reserved, ip.is_multicast, ip.is_unspecified)
    )


def _matches_host_suffix(hostname: str, suffixes: tuple[str, ...]) -> bool:
    return any(hostname == suffix.lstrip(".") or hostname.endswith(suffix) for suffix in suffixes)


def validate_public_url(url: str, *, proxy_host_suffixes: tuple[str, ...] = ()) -> set[str]:
    """Reject unsafe URLs, with a narrow exception for trusted hosts behind the managed egress proxy.

    Returns the resolved, validated address set so callers can pin the actual connection to it
    (see `pin_request_to_addresses`) instead of re-resolving DNS a second time, which would reopen
    a DNS-rebinding window between this check and the real network request.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must be an unauthenticated HTTP(S) URL")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError("URL host could not be resolved") from exc
    proxy_network = ipaddress.ip_network("198.18.0.0/15")
    uses_managed_proxy = (
        parsed.scheme == "https"
        and _matches_host_suffix(parsed.hostname, proxy_host_suffixes)
        and addresses
        and all(ipaddress.ip_address(address) in proxy_network for address in addresses)
    )
    if not addresses or (any(not _is_public_ip(address) for address in addresses) and not uses_managed_proxy):
        raise ValueError("URL host resolves to a non-public address")
    return addresses


def pin_request_to_addresses(url: str, addresses: set[str]) -> tuple[str, dict[str, str], dict[str, str]]:
    """Rewrite `url` to connect directly to one of the pre-validated `addresses`.

    Returns (pinned_url, extra_headers, extensions) to pass to the HTTP client. This closes the
    DNS-rebinding TOCTOU window: the connection uses the exact address `validate_public_url`
    checked, instead of letting the HTTP client re-resolve the hostname (and potentially land on a
    different, unvalidated address) at connect time. The `Host` header and TLS SNI are kept as the
    original hostname so virtual-hosting and certificate validation still work correctly.
    """
    parsed = urlsplit(url)
    hostname = parsed.hostname
    address = next(iter(addresses))
    netloc = f"[{address}]" if ":" in address else address
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username or parsed.password:
        raise ValueError("URL must be an unauthenticated HTTP(S) URL")
    pinned_url = urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment))
    return pinned_url, {"Host": hostname}, {"sni_hostname": hostname}


def resolve_managed_url(source: str) -> Path | None:
    """Resolve loopback-managed API URLs beneath their configured filesystem root."""
    parsed = urlsplit(source)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return None
        path = parsed.path
    else:
        path = source
    for prefix, root in _MANAGED_URL_ROOTS.items():
        if path.startswith(prefix):
            base = root.resolve()
            candidate = (base / unquote(path[len(prefix) :])).resolve()
            if not candidate.is_relative_to(base) or not candidate.is_file():
                raise ValueError("Managed image URL does not identify a valid file")
            return candidate
    return None


def decode_image_bytes(data: bytes, content_type: str | None = None) -> np.ndarray:
    """Validate encoded size, media type, dimensions, and decode an image."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the encoded byte limit")
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type not in _ALLOWED_DATA_MIMES:
            raise ValueError("Response content type is not an allowed image type")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            detected_type = Image.MIME.get(image.format)
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Image data could not be decoded") from exc
    if detected_type not in _ALLOWED_DATA_MIMES:
        raise ValueError("Image format is not allowed")
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Image exceeds the pixel limit")
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Image data could not be decoded")
    return image


def decode_data_uri(source: str) -> np.ndarray:
    """Decode a strict base64 image data URI within configured limits."""
    header, separator, encoded = source.partition(",")
    media_type = header[5:].split(";", 1)[0].lower() if header.startswith("data:") else ""
    if separator != "," or not header.endswith(";base64") or media_type not in _ALLOWED_DATA_MIMES:
        raise ValueError("Only base64 JPEG, PNG, and WebP data URIs are accepted")
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("Image exceeds the encoded byte limit")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Image data URI contains invalid base64") from exc
    return decode_image_bytes(data, media_type)


def download_image(url: str) -> np.ndarray:
    """Download a public image with redirect, streaming-size, and decode checks."""
    current = url
    with httpx.Client(timeout=15.0, follow_redirects=False, trust_env=False) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            addresses = validate_public_url(current)
            pinned_url, pin_headers, extensions = pin_request_to_addresses(current, addresses)
            headers = {"User-Agent": "PersonaWeave-API/0.1.0", **pin_headers}
            with client.stream("GET", pinned_url, headers=headers, extensions=extensions) as response:
                if response.is_redirect:
                    if redirect_count == MAX_REDIRECTS or not response.headers.get("location"):
                        raise ValueError("Image URL has too many redirects")
                    current = urljoin(current, response.headers["location"])
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared and int(declared) > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds the encoded byte limit")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ValueError("Image exceeds the encoded byte limit")
                return decode_image_bytes(bytes(data), response.headers.get("content-type"))
    raise ValueError("Image download failed")


async def download_image_bytes(
    url: str, client: httpx.AsyncClient, *, proxy_host_suffixes: tuple[str, ...] = ()
) -> bytes:
    """Download a provider-returned image using the same SSRF and size policy."""
    current = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            addresses = validate_public_url(current, proxy_host_suffixes=proxy_host_suffixes)
        except ValueError as exc:
            hostname = urlsplit(current).hostname or "unknown"
            raise ValueError(f"Image download rejected host '{hostname}': {exc}") from exc
        pinned_url, pin_headers, extensions = pin_request_to_addresses(current, addresses)
        async with client.stream(
            "GET", pinned_url, headers=pin_headers, extensions=extensions, follow_redirects=False
        ) as response:
            if response.is_redirect:
                if redirect_count == MAX_REDIRECTS or not response.headers.get("location"):
                    raise ValueError("Image URL has too many redirects")
                current = urljoin(current, response.headers["location"])
                continue
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared and int(declared) > MAX_IMAGE_BYTES:
                raise ValueError("Image exceeds the encoded byte limit")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds the encoded byte limit")
            decode_image_bytes(bytes(data), response.headers.get("content-type"))
            return bytes(data)
    raise ValueError("Image download failed")
