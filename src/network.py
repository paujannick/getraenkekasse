"""Netzwerk-Helfer: primäre LAN-IP und externe Basis-URL für QR-Codes."""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse, urlunparse

_LOG = logging.getLogger(__name__)


def primary_lan_ip() -> str:
    """Beste Schätzung für die LAN-IP dieses Rechners.

    Trick: einen UDP-Socket an eine externe Adresse „binden" (kein Paket
    wird gesendet); das Betriebssystem wählt daraufhin das primäre
    Interface. Fallback: ``127.0.0.1``.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "127.0.0.1"
    finally:
        s.close()

    try:
        # sanity: nur echte LAN-Adressen behalten.
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback or addr.is_unspecified:
            return "127.0.0.1"
    except ValueError:
        return "127.0.0.1"
    return ip


def public_base_url() -> str:
    """URL, unter der die Kasse aus dem LAN erreichbar ist.

    Reihenfolge:
    1. ``GK_PUBLIC_URL`` (falls gesetzt – Override, z. B. hinter Reverse Proxy).
    2. ``http://<lan-ip>:<GK_PORT|8000>``.
    """
    override = (os.environ.get("GK_PUBLIC_URL") or "").strip()
    if override:
        return override.rstrip("/")
    port = os.environ.get("GK_PORT", "8000")
    scheme = "https" if os.environ.get("GK_HTTPS") == "1" else "http"
    return f"{scheme}://{primary_lan_ip()}:{port}"


def rewrite_url_host(url: str) -> str:
    """Ersetzt Host/Scheme/Port in einer URL durch die Public-Base-URL.

    Der Pfad und die Query bleiben erhalten. Wird verwendet, um von Flask
    ausgegebene ``_external``-URLs (die häufig ``localhost`` sagen) auf
    den LAN-erreichbaren Wert zu drehen.
    """
    try:
        p = urlparse(url)
        base = urlparse(public_base_url())
    except ValueError:
        return url
    return urlunparse(base._replace(path=p.path, params=p.params,
                                    query=p.query, fragment=p.fragment))
