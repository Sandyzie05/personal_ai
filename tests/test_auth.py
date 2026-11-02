"""Synthetic auth durability tests; HOME always points at fixture-owned data."""

import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.security import auth as auth_module
from src.security.auth import AuthenticationError, AuthManager
from src.security.encryption import AEADEncryption, PasswordKeyDerivation


PASSWORD = "synthetic-password"
NEW_PASSWORD = "synthetic-new-password"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX flock required")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def read_state(home):
    return json.loads((home / ".personal_ai_auth").read_text())


def test_register_wrong_then_right_login_keeps_wrapped_key(isolated_home):
    manager = AuthManager()
    dek = manager.register(PASSWORD)
    original = read_state(isolated_home)
    assert original["iterations"] == PasswordKeyDerivation.ITERATIONS
    assert original["version"] == 2
    assert base64.b64encode(dek).decode() not in json.dumps(original)
    assert PASSWORD not in json.dumps(original)

    with pytest.raises(AuthenticationError, match="Authentication failed"):
        AuthManager().authenticate("wrong-password")
    assert read_state(isolated_home)["failed_attempts"] == 1

    # A new manager models dropping all in-memory authentication/session state.
    assert AuthManager().authenticate(PASSWORD) == dek
    assert read_state(isolated_home) == original


def test_lockout_persists_and_stale_manager_cannot_reset_it(
    isolated_home, monkeypatch
):
    now = 1000.0
    monkeypatch.setattr(auth_module.time, "time", lambda: now)
    stale_manager = AuthManager()
    dek = stale_manager.register(PASSWORD)
    for _ in range(stale_manager.MAX_ATTEMPTS):
        with pytest.raises(AuthenticationError, match="Authentication failed"):
            AuthManager().authenticate("wrong-password")

    locked = read_state(isolated_home)
    assert locked["locked_until"] == now + stale_manager.LOCKOUT_SECONDS
    for manager in (stale_manager, AuthManager()):
        with pytest.raises(AuthenticationError, match="Too many failed attempts"):
            manager.authenticate(PASSWORD)
    assert read_state(isolated_home) == locked

    now += stale_manager.LOCKOUT_SECONDS
    assert stale_manager.authenticate(PASSWORD) == dek
    assert read_state(isolated_home)["locked_until"] == 0
    assert read_state(isolated_home)["failed_attempts"] == 0


def test_password_change_retains_dek_and_rejects_stale_password(isolated_home):
    stale_manager = AuthManager()
    dek = stale_manager.register(PASSWORD)
    old_state = read_state(isolated_home)

    assert AuthManager().change_password(PASSWORD, NEW_PASSWORD) == dek
    changed = read_state(isolated_home)
    assert changed["salt"] != old_state["salt"]
    assert changed["wrapped_key"] != old_state["wrapped_key"]
    assert changed["iterations"] == old_state["iterations"]
    with pytest.raises(AuthenticationError, match="Authentication failed"):
        stale_manager.authenticate(PASSWORD)
    assert stale_manager.authenticate(NEW_PASSWORD) == dek
    assert read_state(isolated_home) == changed


@pytest.mark.parametrize("operation", ["register", "change_password"])
def test_short_password_cannot_replace_existing_account(isolated_home, operation):
    manager = AuthManager()
    dek = manager.register(PASSWORD)
    original = (isolated_home / ".personal_ai_auth").read_bytes()
    with pytest.raises(AuthenticationError, match="at least 8"):
        if operation == "register":
            manager.register("short")
        else:
            manager.change_password(PASSWORD, "short")
    assert (isolated_home / ".personal_ai_auth").read_bytes() == original
    assert AuthManager().authenticate(PASSWORD) == dek


def test_existing_registration_is_not_overwritten(isolated_home):
    manager = AuthManager()
    dek = manager.register(PASSWORD)
    with pytest.raises(AuthenticationError, match="already registered"):
        AuthManager().register(NEW_PASSWORD)
    assert manager.authenticate(PASSWORD) == dek


@pytest.mark.parametrize("stage", ["serialize", "fsync", "replace"])
def test_interrupted_update_preserves_old_file_and_cleans_staging(
    isolated_home, monkeypatch, stage
):
    manager = AuthManager()
    dek = manager.register(PASSWORD)
    path = isolated_home / ".personal_ai_auth"
    original = path.read_bytes()

    def interrupt(*args, **kwargs):
        if stage == "serialize":
            args[1].write('{"partial":')
        raise OSError("synthetic interrupted persist")

    with monkeypatch.context() as patch:
        if stage == "serialize":
            patch.setattr(auth_module.json, "dump", interrupt)
        else:
            patch.setattr(auth_module.os, stage, interrupt)
        with pytest.raises(OSError, match="synthetic interrupted persist"):
            manager.authenticate("wrong-password")

    assert path.read_bytes() == original
    assert not list(isolated_home.glob(".personal_ai_auth.*.tmp"))
    assert AuthManager().authenticate(PASSWORD) == dek


def test_interrupted_registration_leaves_no_partial_account(isolated_home, monkeypatch):
    def interrupt(*args):
        raise OSError("synthetic replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(auth_module.os, "replace", interrupt)
        with pytest.raises(OSError, match="synthetic replace failure"):
            AuthManager().register(PASSWORD)
    assert not AuthManager().is_registered()
    assert not list(isolated_home.glob(".personal_ai_auth.*.tmp"))
    dek = AuthManager().register(PASSWORD)
    assert AuthManager().authenticate(PASSWORD) == dek


def test_interrupted_password_rewrap_preserves_original_password(
    isolated_home, monkeypatch
):
    manager = AuthManager()
    dek = manager.register(PASSWORD)
    original = (isolated_home / ".personal_ai_auth").read_bytes()
    real_replace = os.replace
    writes = 0

    def replace(source, destination):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("synthetic rewrap failure")
        real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(auth_module.os, "replace", replace)
        with pytest.raises(OSError, match="synthetic rewrap failure"):
            manager.change_password(PASSWORD, NEW_PASSWORD)
    assert writes == 2
    assert (isolated_home / ".personal_ai_auth").read_bytes() == original
    assert not list(isolated_home.glob(".personal_ai_auth.*.tmp"))
    assert AuthManager().authenticate(PASSWORD) == dek
    with pytest.raises(AuthenticationError, match="Authentication failed"):
        AuthManager().authenticate(NEW_PASSWORD)


def test_staging_is_private_and_synced_before_atomic_replace(
    isolated_home, monkeypatch
):
    real_replace = os.replace
    real_fsync = os.fsync
    events = []

    def sync(fd):
        events.append("directory-sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "sync")
        real_fsync(fd)

    def replace(source, destination):
        source = Path(source)
        assert source.parent == isolated_home
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert json.loads(source.read_text())["version"] == 2
        assert events[-1] == "sync"
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(auth_module.os, "fsync", sync)
    monkeypatch.setattr(auth_module.os, "replace", replace)
    manager = AuthManager()
    manager.register(PASSWORD)
    assert events == ["sync", "replace", "directory-sync"]
    manager.authenticate(PASSWORD)
    manager.change_password(PASSWORD, NEW_PASSWORD)
    for name in (".personal_ai_auth", ".personal_ai_auth.lock"):
        assert stat.S_IMODE((isolated_home / name).stat().st_mode) == 0o600


def test_directory_sync_error_reports_already_committed_update(
    isolated_home, monkeypatch
):
    manager = AuthManager()
    dek = manager.register(PASSWORD)
    real_fsync = os.fsync

    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic directory sync failure")
        real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(auth_module.os, "fsync", sync)
        with pytest.raises(OSError, match="synthetic directory sync failure"):
            manager.authenticate("wrong-password")
    assert read_state(isolated_home)["failed_attempts"] == 1
    assert not list(isolated_home.glob(".personal_ai_auth.*.tmp"))
    assert AuthManager().authenticate(PASSWORD) == dek


def test_legacy_iterations_and_wrap_format_remain_readable(isolated_home):
    manager = AuthManager()
    dek = manager.register(PASSWORD)
    state = read_state(isolated_home)
    state["iterations"] = 100_000
    kek = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=base64.b64decode(state["salt"]),
        iterations=state["iterations"],
    ).derive(PASSWORD.encode())
    state["wrapped_key"] = base64.b64encode(
        AEADEncryption(key=kek).encrypt(dek)
    ).decode()
    (isolated_home / ".personal_ai_auth").write_text(json.dumps(state))

    assert AuthManager().authenticate(PASSWORD) == dek
    assert read_state(isolated_home) == state


def test_simultaneous_managers_preserve_every_failure(isolated_home):
    AuthManager().register(PASSWORD)
    workers = 4
    ready = Barrier(workers)

    def fail_once():
        manager = AuthManager()
        ready.wait(timeout=20)
        with pytest.raises(AuthenticationError, match="Authentication failed"):
            manager.authenticate("wrong-password")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda _: fail_once(), range(workers)))
    assert read_state(isolated_home)["failed_attempts"] == workers


def test_simultaneous_registrations_create_only_one_dek(isolated_home):
    ready = Barrier(2)

    def register(password):
        ready.wait(timeout=20)
        try:
            return password, AuthManager().register(password)
        except AuthenticationError as error:
            assert str(error) == "User already registered."
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, (PASSWORD, NEW_PASSWORD)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    password, dek = winners[0]
    assert AuthManager().authenticate(password) == dek


def test_simultaneous_password_changes_recheck_current_password(isolated_home):
    dek = AuthManager().register(PASSWORD)
    ready = Barrier(2)

    def change(password):
        ready.wait(timeout=20)
        try:
            assert AuthManager().change_password(PASSWORD, password) == dek
            return password
        except AuthenticationError as error:
            assert str(error) == "Authentication failed."
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(change, (NEW_PASSWORD, "synthetic-other-password"))
        )
    winners = [password for password in results if password is not None]
    assert len(winners) == 1
    assert AuthManager().authenticate(winners[0]) == dek


def test_processes_serialize_failure_counters(isolated_home):
    AuthManager().register(PASSWORD)
    worker = """
import sys
from src.security.auth import AuthManager, AuthenticationError
manager = AuthManager()
print("ready", flush=True)
sys.stdin.readline()
try:
    manager.authenticate("wrong-password")
except AuthenticationError as error:
    if str(error) != "Authentication failed.":
        raise
else:
    raise AssertionError("wrong password accepted")
"""
    processes = []
    try:
        for _ in range(4):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", worker],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env={**os.environ, "HOME": str(isolated_home)},
                )
            )
        for process in processes:
            process.stdin.write("start\n")
            process.stdin.flush()
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            assert stdout.strip() == "ready"
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
    assert read_state(isolated_home)["failed_attempts"] == 4


def test_unsupported_platform_fails_closed(isolated_home, monkeypatch):
    monkeypatch.setattr(auth_module, "fcntl", None)
    with pytest.raises(AuthenticationError, match="POSIX file locking"):
        AuthManager().register(PASSWORD)
    assert not (isolated_home / ".personal_ai_auth").exists()
    assert not (isolated_home / ".personal_ai_auth.lock").exists()
    assert not list(isolated_home.glob(".personal_ai_auth.*.tmp"))
