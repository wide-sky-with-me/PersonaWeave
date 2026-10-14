// Decision: .agents/notes/platform/20261014-tailnet-real-dns-clash.md
// Install only as this Mac's global extension after backing up its existing script.
function main(config) {
  const dns = config.dns || {};
  const filterMode = dns['fake-ip-filter-mode'] || 'blacklist';
  if (filterMode !== 'blacklist') {
    throw new Error('Review the existing DNS filter semantics before installing this extension.');
  }
  const directRules = [
    'PROCESS-NAME,Tailscale,DIRECT',
    'PROCESS-NAME,io.tailscale.ipn.macsys.network-extension,DIRECT',
    'DOMAIN-SUFFIX,tailscale.com,DIRECT',
    'DOMAIN-SUFFIX,tailscale.io,DIRECT',
    'DOMAIN-SUFFIX,ts.net,DIRECT',
    'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve',
    'IP-CIDR6,fd7a:115c:a1e0::/48,DIRECT,no-resolve',
  ];
  config.rules = [...directRules, ...(config.rules || []).filter((rule) => !directRules.includes(rule))];

  dns['fake-ip-filter'] = [...new Set([
    ...(dns['fake-ip-filter'] || []),
    '+.tailscale.com',
    '+.tailscale.io',
    '+.ts.net',
    '+.dmxapi.cn',
  ])];
  dns['nameserver-policy'] = { ...(dns['nameserver-policy'] || {}), '+.ts.net': '100.100.100.100' };
  config.dns = dns;

  const tun = config.tun || {};
  tun['route-exclude-address'] = [...new Set([
    ...(tun['route-exclude-address'] || []),
    '100.64.0.0/10',
    'fd7a:115c:a1e0::/48',
  ])];
  config.tun = tun;
  return config;
}
