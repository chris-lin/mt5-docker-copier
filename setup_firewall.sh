#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "[ERROR] Please run this script as root or with sudo."
  exit 1
fi

echo "=========================================="
echo " Setting up Firewall Rules for MT5 Copier "
echo "=========================================="

echo "[1/2] Allowing traffic from Docker bridge interfaces..."
iptables -C INPUT -i docker0 -j ACCEPT 2>/dev/null || iptables -I INPUT -i docker0 -j ACCEPT
iptables -C INPUT -i br-+ -j ACCEPT 2>/dev/null || iptables -I INPUT -i br-+ -j ACCEPT

echo "[2/2] Persisting iptables rules..."
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save
elif command -v iptables-save >/dev/null 2>&1; then
  mkdir -p /etc/iptables
  iptables-save > /etc/iptables/rules.v4
  echo "Rules saved to /etc/iptables/rules.v4"
fi

echo "=========================================="
echo " Firewall configuration completed! "
echo " Containers can now connect to 172.18.0.1:9001 / 9002."
echo "=========================================="
