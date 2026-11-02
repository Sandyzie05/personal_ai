"""Keep regression tests away from real vaults, dotenv files and services."""

import ipaddress
import socket
import tempfile

import dotenv
import pytest

_SESSION_ISOLATION = pytest.StashKey[tuple]()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "loopback: permits only synthetic loopback-server connections"
    )
    home = tempfile.TemporaryDirectory(prefix="personal-ai-tests-")
    patch = pytest.MonkeyPatch()
    patch.setenv("HOME", home.name)
    patch.setenv("PERSONAL_AI_SKILLS_DIR", "~/.personal_ai/skills")
    for name in (
        "OMLX_API_KEY",
        "PERSONAL_AI_OMLX_API_KEY",
        "PERSONAL_AI_FRED_API_KEY",
        "PERSONAL_AI_ALPHA_VANTAGE_API_KEY",
        "PERSONAL_AI_SEC_USER_AGENT",
    ):
        patch.delenv(name, raising=False)
    patch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    config.stash[_SESSION_ISOLATION] = home, patch


def pytest_unconfigure(config):
    home, patch = config.stash[_SESSION_ISOLATION]
    patch.undo()
    home.cleanup()


@pytest.fixture(autouse=True)
def isolated_services(tmp_path, monkeypatch, request):
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PERSONAL_AI_SKILLS_DIR", str(home / "skills"))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    connect = socket.socket.connect
    getaddrinfo = socket.getaddrinfo
    allow_loopback = request.node.get_closest_marker("loopback") is not None

    def is_loopback(host):
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def guarded_connect(sock, address):
        if allow_loopback and isinstance(address, tuple):
            host = address[0]
            if is_loopback(host):
                return connect(sock, address)
        raise AssertionError("Unexpected network connection in offline test")

    def blocked_send(*args, **kwargs):
        raise AssertionError("Unexpected network connection in offline test")

    def guarded_lookup(host, *args, **kwargs):
        if allow_loopback and is_loopback(host):
            return getaddrinfo(host, *args, **kwargs)
        raise AssertionError("Unexpected DNS lookup in offline test")

    monkeypatch.setattr(socket, "getaddrinfo", guarded_lookup)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_send)
    monkeypatch.setattr(socket.socket, "sendto", blocked_send)
