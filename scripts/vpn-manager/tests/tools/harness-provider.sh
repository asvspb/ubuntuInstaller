#!/usr/bin/env bash
# harness-provider.sh — guaranteed provider baseline for failover experiments.
#
# WHY: while testing failover we repeatedly lost connectivity and it was never
# obvious who did it (our DROP rule, the split's nft capture of Cloudflare
# ranges, a broken DoH chain, or the agent's own `vpn down`).  This script
# makes the provider path explicit and reversible, and never leaves a test
# artefact behind.
#
# Usage:
#   harness-provider.sh state        # what is up right now
#   harness-provider.sh provider-on  # teardown tunnel + automation, DNS via ISP
#   harness-provider.sh tunnel-on    # direct xray-split (asv2-reality)
#   harness-provider.sh drop-on|off  # block/allow exit:443 (tunnel-only effect)
#   harness-provider.sh check        # classify provider reachability
set -uo pipefail

EXIT_IP="${EXIT_IP:-213.108.23.119}"
SPLIT_SCRIPT="${SPLIT_SCRIPT:-/home/asv-spb/Dev/ubuntuInstaller/scripts/17_xray-split.sh}"

say() { printf '%s\n' "$*"; }

state() {
  say "== tunnel"
  ip -br link show xray0 2>/dev/null | head -1 || say "xray0: нет"
  sudo -n systemctl is-active vpn-xray-split@asv2-reality 2>/dev/null \
    | sed 's/^/vpn-xray-split@asv2-reality: /'
  say "== automation"
  systemctl --user is-active vpn-failover.timer vpn-failover.path 2>/dev/null \
    | tr '\n' ' '; say ""
  python3 - <<'PY'
import os, json
p = os.path.expanduser('~/.config/vpn-manager/guard.json')
try:
    cfg = json.load(open(p)).get('failover') or {}
    print("failover.enabled:", cfg.get('enabled', False))
except OSError:
    print("guard.json: нет")
PY
  say "== test artefacts"
  if sudo -n iptables -S OUTPUT 2>/dev/null | grep -q "$EXIT_IP"; then
    say "DROP $EXIT_IP: ЕСТЬ (снять: harness-provider.sh drop-off)"
  else
    say "DROP $EXIT_IP: нет"
  fi
  say "== dns"
  resolvectl status 2>/dev/null | grep -m1 "Current DNS Server" || true
  say "== egress"
  say "provider: $(curl -4 --max-time 6 -s -o /dev/null -w '%{http_code}' https://api.ipify.org)"
}

provider_on() {
  say "-- выключаю автоматику failover (юниты + флаг)"
  vpn guard failover disable --apply >/dev/null 2>&1 || true
  say "-- снимаю тестовые DROP-правила"
  while sudo -n iptables -S OUTPUT 2>/dev/null | grep -q -- "-d $EXIT_IP/32"; do
    sudo -n iptables -D OUTPUT -p tcp -d "$EXIT_IP" --dport 443 -j DROP 2>/dev/null || break
  done
  say "-- снимаю xray-split (nft-набор + tun + ip rule)"
  sudo -n bash "$SPLIT_SCRIPT" --off >/dev/null 2>&1 || true
  say "-- DNS обратно к провайдеру (без локальной петли 127.0.0.1)"
  vpn doh off >/dev/null 2>&1 || true
  sudo -n systemctl restart systemd-resolved 2>/dev/null || true
  sleep 1
  say "-- проверка"
  check
}

tunnel_on() {
  say "-- поднимаю прямой tun (vpn scheme xray-split asv2-reality)"
  vpn scheme xray-split asv2-reality
}

drop_on() {
  sudo -n iptables -I OUTPUT 1 -p tcp -d "$EXIT_IP" --dport 443 -j DROP \
    && say "DROP $EXIT_IP:443 включён"
}

drop_off() {
  while sudo -n iptables -S OUTPUT 2>/dev/null | grep -q -- "-d $EXIT_IP/32"; do
    sudo -n iptables -D OUTPUT -p tcp -d "$EXIT_IP" --dport 443 -j DROP 2>/dev/null || break
  done
  say "DROP $EXIT_IP:443 снят"
}

check() {
  local targets=(https://api.ipify.org https://ya.ru https://example.com)
  local failures=0
  for url in "${targets[@]}"; do
    local rc code
    code=$(curl -4 -s -o /dev/null --max-time 8 -w '%{http_code}' "$url"); rc=$?
    printf '  %-26s rc=%-2s http=%s\n' "$url" "$rc" "$code"
    [ "$rc" -ne 0 ] && failures=$((failures + 1))
  done
  # DNS itself must answer; a dead resolver looks like a dead tunnel
  if dig +short +time=3 www.youtube.com >/dev/null 2>&1 \
     && [ -n "$(dig +short +time=3 www.youtube.com | head -1)" ]; then
    say "  dns: ok"
  else
    say "  dns: DEAD (это локальный резолвер, не туннель)"
    failures=$((failures + 1))
  fi
  if [ "$failures" -eq 0 ]; then
    say "PROVIDER: ok"
    return 0
  fi
  say "PROVIDER: $failures из $(( ${#targets[@]} + 1 )) проверок упали (см. rc)"
  return 1
}

case "${1:-state}" in
  state) state ;;
  provider-on) provider_on ;;
  tunnel-on) tunnel_on ;;
  drop-on) drop_on ;;
  drop-off) drop_off ;;
  check) check ;;
  *) say "usage: $0 state|provider-on|tunnel-on|drop-on|drop-off|check"; exit 2 ;;
esac
