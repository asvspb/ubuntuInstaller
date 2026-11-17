"""Доказательство работы предохранителя: тест не может дёрнуть систему.

Инцидент 2026-11-18: pytest реально выполнил ``17_xray-split.sh --off`` и
``systemctl stop vpn-awg-split@relay.service`` и снёс живой туннель. Здесь
фиксируем контракт guard-фикстуры из ``tests/sys_guard.py``:

- system-мутирующая команда (в т.ч. под ``sudo -n``) → ``SystemCommandBlocked``;
- безопасная команда (``/bin/true``, ``gsettings``, ``dig``) проходит;
- ``bash -n <script>`` (только синтаксис) пропускается, а запуск split-скрипта нет;
- ``VPNM_TEST_ALLOW_SYSTEM=1`` — осознанный обход, вызов уходит в реальный
  runner (в тесте подменён, чтобы ничего не исполнить).
"""

from __future__ import annotations

import subprocess

import pytest

import sys_guard


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_blocks_systemctl_under_sudo():
    with pytest.raises(sys_guard.SystemCommandBlocked, match="systemctl"):
        sys_guard.check_command(
            ["sudo", "-n", "systemctl", "restart", "systemd-resolved"])


def test_blocks_each_forbidden_binary():
    for binary in sys_guard.FORBIDDEN_BINARIES:
        with pytest.raises(sys_guard.SystemCommandBlocked):
            sys_guard.check_command([binary, "--probe"])


def test_blocks_split_script_run():
    with pytest.raises(sys_guard.SystemCommandBlocked,
                       match="17_xray-split.sh"):
        sys_guard.check_command(
            ["sudo", "-n", "bash", "/opt/s/17_xray-split.sh", "--off"])


@pytest.mark.parametrize("verb", sorted(sys_guard.SUDO_MUTATION_VERBS))
def test_blocks_sudo_mutation_verbs(verb):
    with pytest.raises(sys_guard.SystemCommandBlocked):
        sys_guard.check_command(["sudo", "-n", verb, "/etc/x"])


def test_blocks_incident_rm_of_tmp_dropin():
    """Строка из journal инцидента 2026-11-18: sudo rm tmp drop-in."""
    with pytest.raises(sys_guard.SystemCommandBlocked):
        sys_guard.check_command(
            ["sudo", "-n", "rm",
             "/tmp/pytest-of-x/pytest-1/test_cli_repair_dns_off0/"
             "resolved.conf.d/vpn-dns-fix.conf"])


def test_blocks_real_subprocess_run_end_to_end():
    """Guard-фикстура реально перехватывает subprocess.run (autouse)."""
    with pytest.raises(sys_guard.SystemCommandBlocked):
        subprocess.run(["sudo", "-n", "systemctl", "restart", "systemd-resolved"],
                       capture_output=True, text=True)


def test_blocks_popen_end_to_end():
    with pytest.raises(sys_guard.SystemCommandBlocked):
        subprocess.Popen(["systemctl", "stop", "vpn-awg-split@relay.service"])


@pytest.mark.parametrize("cmd", [
    ["/bin/true"],
    ["gsettings", "get", "org.gnome.system.proxy", "mode"],
    ["dig", "+short", "@127.0.0.1", "example.com"],
    ["bash", "-n", "/opt/s/17_xray-split.sh"],
    ["curl", "--socks5-hostname", "127.0.0.1:2080", "https://api.ipify.org"],
])
def test_allows_safe_commands(cmd):
    sys_guard.check_command(cmd)  # не бросает


def test_env_bypass_wraps_real_runner_without_touching_system(monkeypatch):
    """VPNM_TEST_ALLOW_SYSTEM=1 пропускает; реальный вызов подменён стабом."""
    monkeypatch.setenv(sys_guard.ALLOW_ENV, "1")
    seen: list = []

    def fake_real_run(*args, **kwargs):
        seen.append(args[0])
        return _FakeCompleted(returncode=0, stdout="ok")

    monkeypatch.setattr(sys_guard, "_REAL_RUN", fake_real_run)
    proc = subprocess.run(
        ["sudo", "-n", "systemctl", "restart", "systemd-resolved"],
        capture_output=True, text=True)
    assert proc.returncode == 0
    assert seen == [["sudo", "-n", "systemctl", "restart", "systemd-resolved"]]


def test_env_bypass_popen_passthrough(monkeypatch):
    monkeypatch.setenv(sys_guard.ALLOW_ENV, "1")
    seen: list = []

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            seen.append(args[0] if args else kwargs.get("args"))

    monkeypatch.setattr(sys_guard, "_REAL_POPEN", _FakePopen)
    subprocess.Popen(["systemctl", "stop", "vpn-awg-split@relay.service"])
    assert seen == [["systemctl", "stop", "vpn-awg-split@relay.service"]]


def test_default_is_forbidden_without_env(monkeypatch):
    monkeypatch.delenv(sys_guard.ALLOW_ENV, raising=False)
    with pytest.raises(sys_guard.SystemCommandBlocked):
        sys_guard.check_command(["systemctl", "stop", "x"])
