"""Exercise startup ordering and restart guards without a Mac or live VM."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/macos-colima-boot-guard.sh"
ADDRESS = "100.88.228.28"
RULE = f"portForwards:\n  - guestIP: {ADDRESS}\n    hostIP: {ADDRESS}\n"


@pytest.fixture
def guard(tmp_path):
    """Replace external commands while executing the actual POSIX shell guard."""
    state = tmp_path / "state.json"
    calls = tmp_path / "calls.txt"
    config = tmp_path / "lima.yaml"
    state.write_text(json.dumps({"address_ready": True, "running": True}))
    config.write_text("portForwards: []\n")
    command = tmp_path / "command"
    command.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "state = json.loads(Path(os.environ['MOCK_STATE']).read_text())\n"
        "name, *args = sys.argv[1:]\n"
        "with Path(os.environ['MOCK_CALLS']).open('a') as log: log.write(name + '\\n')\n"
        "if name == 'ifconfig':\n"
        f"    print('inet {ADDRESS}' if state.get('address_ready', True) else 'inet 127.0.0.1')\n"
        "elif name == 'colima':\n"
        "    sys.exit(0 if state.get('running', True) else 1)\n"
        "elif name == 'docker':\n"
        "    if not state.get('docker_ready', True): sys.exit(1)\n"
        "    if 'ps' in args: print(state.get('containers', 'personaweave-personaweave-1'))\n"
        "    elif 'exec' in args: sys.exit(1 if state.get('busy', False) else 0)\n"
        "elif name == 'brew':\n"
        "    assert args == ['services', 'restart', 'colima']\n"
        "    sys.exit(1 if state.get('brew_failed', False) else 0)\n"
    )
    command.chmod(0o755)
    env = {
        **os.environ,
        "PERSONAWEAVE_BIND_ADDRESS": ADDRESS,
        "PERSONAWEAVE_DEPLOYMENT_DIR": str(tmp_path),
        "PERSONAWEAVE_LIMA_CONFIG": str(config),
        "MOCK_STATE": str(state),
        "MOCK_CALLS": str(calls),
    }
    for name in ["ifconfig", "colima", "docker", "brew"]:
        wrapper = tmp_path / name
        wrapper.write_text(f'#!/bin/sh\nexec "{command}" {name} "$@"\n')
        wrapper.chmod(0o755)
        env[f"PERSONAWEAVE_{name.upper()}_BIN"] = str(wrapper)

    def run(**updates):
        settings = json.loads(state.read_text())
        settings.update(updates)
        state.write_text(json.dumps(settings))
        return subprocess.run(["/bin/sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10)

    def restarted():
        return calls.read_text().splitlines().count("brew") if calls.exists() else 0

    return run, restarted, config


@pytest.mark.parametrize(
    "settings",
    [
        {"address_ready": False},
        {"running": False},
        {"docker_ready": False},
        {"busy": True},
        {"containers": "personaweave-personaweave-1\nunrelated-container"},
        {"containers": ""},
    ],
)
def test_defers_until_address_vm_and_dedicated_idle_container_are_ready(guard, settings):
    run, restarted, _ = guard
    assert run(**settings).returncode == 0
    assert restarted() == 0


def test_healthy_rules_are_an_idempotent_noop(guard):
    run, restarted, config = guard
    config.write_text(RULE)
    assert run().returncode == 0
    assert run().returncode == 0
    assert restarted() == 0


@pytest.mark.parametrize("rule", ["portForwards: []\n", f"guestIP: {ADDRESS}\n", f"hostIP: {ADDRESS}\n"])
def test_missing_rule_repairs_once_and_respects_cooldown(guard, rule):
    run, restarted, config = guard
    config.write_text(rule)
    assert run().returncode == 0
    assert restarted() == 1
    assert run().returncode == 0
    assert restarted() == 1


def test_missing_config_is_not_an_excuse_to_restart(guard):
    run, restarted, config = guard
    config.unlink()
    assert run().returncode == 0
    assert restarted() == 0


def test_failed_restart_is_visible_and_does_not_loop(guard):
    run, restarted, _ = guard
    assert run(brew_failed=True).returncode == 1
    assert restarted() == 1
    assert run().returncode == 0
    assert restarted() == 1
