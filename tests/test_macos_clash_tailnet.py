import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "macos-clash-tailnet.js"


def transform(config: dict, repeat: bool = False) -> dict:
    code = """
const fs = require('fs');
const vm = require('vm');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const config = JSON.parse(process.argv[2]);
try {
  const result = context.main(config);
  if (process.argv[3] === 'repeat') context.main(result);
  process.stdout.write(JSON.stringify({ result }));
} catch (error) {
  process.stdout.write(JSON.stringify({ error: error.message, result: config }));
}
"""
    result = subprocess.run(
        ["node", "-e", code, str(SCRIPT), json.dumps(config), "repeat" if repeat else "once"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_preserves_subscription_and_prior_network_settings():
    config = {
        "proxies": [{"name": "existing", "type": "direct"}],
        "rules": ["DOMAIN,existing.example,Existing", "MATCH,Existing"],
        "dns": {
            "enhanced-mode": "fake-ip",
            "fake-ip-filter": ["+.existing.example"],
            "nameserver": ["223.5.5.5"],
            "nameserver-policy": {"+.existing.example": "223.5.5.5"},
        },
        "tun": {"enable": True, "stack": "gvisor", "route-exclude-address": ["192.168.0.0/16"]},
    }
    result = transform(config)["result"]
    assert result["proxies"] == config["proxies"]
    assert result["rules"][-2:] == config["rules"]
    assert result["dns"]["nameserver"] == config["dns"]["nameserver"]
    assert "+.existing.example" in result["dns"]["fake-ip-filter"]
    assert result["dns"]["nameserver-policy"]["+.existing.example"] == "223.5.5.5"
    assert result["dns"]["nameserver-policy"]["+.ts.net"] == "100.100.100.100"
    assert "192.168.0.0/16" in result["tun"]["route-exclude-address"]
    assert result["tun"]["stack"] == "gvisor"


def test_prepends_direct_process_domains_and_both_tailnet_ranges():
    result = transform({"rules": ["MATCH,Existing"]})["result"]
    assert result["rules"][:2] == [
        "PROCESS-NAME,Tailscale,DIRECT",
        "PROCESS-NAME,io.tailscale.ipn.macsys.network-extension,DIRECT",
    ]
    assert "DOMAIN-SUFFIX,tailscale.com,DIRECT" in result["rules"]
    assert "DOMAIN-SUFFIX,tailscale.io,DIRECT" in result["rules"]
    assert "DOMAIN-SUFFIX,ts.net,DIRECT" in result["rules"]
    assert "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve" in result["rules"]
    assert "IP-CIDR6,fd7a:115c:a1e0::/48,DIRECT,no-resolve" in result["rules"]
    assert result["rules"][-1] == "MATCH,Existing"


def test_real_ip_filter_keeps_fake_ip_enabled_for_other_domains():
    result = transform({"dns": {"enhanced-mode": "fake-ip"}})["result"]
    assert result["dns"]["enhanced-mode"] == "fake-ip"
    assert set(result["dns"]["fake-ip-filter"]) == {
        "+.tailscale.com",
        "+.tailscale.io",
        "+.ts.net",
        "+.dmxapi.cn",
    }


def test_repeat_does_not_duplicate_rules_filters_or_ranges():
    config = {"rules": ["MATCH,Existing"], "dns": {"fake-ip-filter": ["+.existing.example"]}}
    assert transform(config)["result"] == transform(config, repeat=True)["result"]


@pytest.mark.parametrize("mode", ["whitelist", "rule"])
def test_unknown_filter_semantics_fail_without_mutating_config(mode):
    config = {"rules": ["MATCH,Existing"], "dns": {"fake-ip-filter-mode": mode}}
    result = transform(config)
    assert "error" in result
    assert result["result"] == config
