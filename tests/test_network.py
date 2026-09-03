from __future__ import annotations

import ipaddress

from src import network


def test_public_base_url_uses_env(monkeypatch):
    monkeypatch.setenv("GK_PUBLIC_URL", "https://kasse.example.com/")
    assert network.public_base_url() == "https://kasse.example.com"


def test_public_base_url_lan_default(monkeypatch):
    monkeypatch.delenv("GK_PUBLIC_URL", raising=False)
    monkeypatch.delenv("GK_HTTPS", raising=False)
    monkeypatch.setenv("GK_PORT", "8080")
    url = network.public_base_url()
    # Muss http://<irgendeine-ipv4>:8080 sein.
    assert url.startswith("http://")
    host = url[len("http://"):].split(":")[0]
    ipaddress.ip_address(host)  # akzeptiert v4 oder v6
    assert url.endswith(":8080")


def test_rewrite_url_host_keeps_path(monkeypatch):
    monkeypatch.setenv("GK_PUBLIC_URL", "http://10.0.0.5:8000")
    out = network.rewrite_url_host("http://127.0.0.1:8000/me/xyz?a=1")
    assert out == "http://10.0.0.5:8000/me/xyz?a=1"
