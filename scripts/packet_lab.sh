#!/usr/bin/env bash
# Real Linux packets through an isolated pair of network namespaces.
# Requires root, CAP_NET_ADMIN, iproute2, iperf3, and iputils-ping.
set -Eeuo pipefail

CLIENT_NS="nexqos-packet-client"
SERVER_NS="nexqos-packet-server"
CLIENT_IF="nexqos-c"
SERVER_IF="nexqos-s"
TMP_DIR="$(mktemp -d)"
CLIENT_CREATED=0
SERVER_CREATED=0
SERVER4_PID=""
SERVER6_PID=""
SERVER4_RT_PID=""
SERVER6_RT_PID=""
VETH_CREATED=0
REPORT_DIR="${1:-reports}"

cleanup() {
  [[ -n "$SERVER4_PID" ]] && kill "$SERVER4_PID" 2>/dev/null || true
  [[ -n "$SERVER6_PID" ]] && kill "$SERVER6_PID" 2>/dev/null || true
  [[ -n "$SERVER4_RT_PID" ]] && kill "$SERVER4_RT_PID" 2>/dev/null || true
  [[ -n "$SERVER6_RT_PID" ]] && kill "$SERVER6_RT_PID" 2>/dev/null || true
  if [[ "$VETH_CREATED" == 1 ]]; then
    ip link show dev "$CLIENT_IF" >/dev/null 2>&1 && ip link del dev "$CLIENT_IF" 2>/dev/null || true
    ip link show dev "$SERVER_IF" >/dev/null 2>&1 && ip link del dev "$SERVER_IF" 2>/dev/null || true
  fi
  [[ "$CLIENT_CREATED" == 1 ]] && ip netns del "$CLIENT_NS" 2>/dev/null || true
  [[ "$SERVER_CREATED" == 1 ]] && ip netns del "$SERVER_NS" 2>/dev/null || true
  rm -rf -- "$TMP_DIR"
}
trap cleanup EXIT INT TERM

for cmd in ip tc iperf3 ping; do
  command -v "$cmd" >/dev/null || { echo "Missing dependency: $cmd" >&2; exit 2; }
done
[[ "$(id -u)" == 0 ]] || { echo "Run this lab as root (it only changes its private namespaces)." >&2; exit 2; }
mkdir -p -- "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/packet-lab-$(date -u +%Y%m%dT%H%M%S%N)Z.log"
exec > >(tee "$REPORT_FILE") 2>&1
ip netns list | grep -Eq "^${CLIENT_NS}([[:space:]]|$)" && { echo "Namespace $CLIENT_NS already exists; refusing to reuse it." >&2; exit 2; }
ip netns list | grep -Eq "^${SERVER_NS}([[:space:]]|$)" && { echo "Namespace $SERVER_NS already exists; refusing to reuse it." >&2; exit 2; }
ip link show dev "$CLIENT_IF" >/dev/null 2>&1 && { echo "Interface $CLIENT_IF already exists; refusing to reuse it." >&2; exit 2; }
ip link show dev "$SERVER_IF" >/dev/null 2>&1 && { echo "Interface $SERVER_IF already exists; refusing to reuse it." >&2; exit 2; }

ip netns add "$CLIENT_NS"
CLIENT_CREATED=1
ip netns add "$SERVER_NS"
SERVER_CREATED=1
ip link add "$CLIENT_IF" type veth peer name "$SERVER_IF"
VETH_CREATED=1
ip link set "$CLIENT_IF" netns "$CLIENT_NS"
ip link set "$SERVER_IF" netns "$SERVER_NS"

ip -n "$CLIENT_NS" link set "$CLIENT_IF" name eth0
ip -n "$SERVER_NS" link set "$SERVER_IF" name eth0
for ns in "$CLIENT_NS" "$SERVER_NS"; do ip -n "$ns" link set lo up; done
ip -n "$CLIENT_NS" address add 192.0.2.1/24 dev eth0
ip -n "$SERVER_NS" address add 192.0.2.2/24 dev eth0
ip -n "$CLIENT_NS" -6 address add 2001:db8:201::1/64 dev eth0
ip -n "$SERVER_NS" -6 address add 2001:db8:201::2/64 dev eth0
ip -n "$CLIENT_NS" link set eth0 up
ip -n "$SERVER_NS" link set eth0 up

ip netns exec "$SERVER_NS" iperf3 -4 -s -p 5201 >"$TMP_DIR/iperf4.log" 2>&1 & SERVER4_PID=$!
ip netns exec "$SERVER_NS" iperf3 -6 -s -p 5202 >"$TMP_DIR/iperf6.log" 2>&1 & SERVER6_PID=$!
ip netns exec "$SERVER_NS" iperf3 -4 -s -p 5203 >"$TMP_DIR/iperf4-rt.log" 2>&1 & SERVER4_RT_PID=$!
ip netns exec "$SERVER_NS" iperf3 -6 -s -p 5204 >"$TMP_DIR/iperf6-rt.log" 2>&1 & SERVER6_RT_PID=$!
sleep 1

install_fifo() {
  tc qdisc replace dev eth0 root handle 1: tbf rate 20mbit burst 32k latency 400ms
}

install_adaptive() {
  tc qdisc replace dev eth0 root handle 1: htb default 20
  tc class add dev eth0 parent 1: classid 1:1 htb rate 20mbit ceil 20mbit
  tc class add dev eth0 parent 1:1 classid 1:10 htb rate 3mbit ceil 3mbit prio 1
  tc class add dev eth0 parent 1:1 classid 1:20 htb rate 17mbit ceil 20mbit prio 5
  tc qdisc add dev eth0 parent 1:10 handle 10: fq_codel
  tc qdisc add dev eth0 parent 1:20 handle 20: fq_codel
  tc filter add dev eth0 protocol ip parent 1: prio 1 flower ip_tos 0xb8/0xfc flowid 1:10
  tc filter add dev eth0 protocol ipv6 parent 1: prio 1 flower ip_tos 0xb8/0xfc flowid 1:10
}

run_case() {
  local policy="$1" family="$2" destination="$3" port="$4" rtport="$5" ping_flag="$6"
  if [[ "$policy" == baseline ]]; then install_fifo; else install_adaptive; fi
  echo
  echo "=== $policy · IPv$family · TCP bulk + DSCP EF UDP media + ping · 20 Mbps bottleneck ==="
  ip netns exec "$CLIENT_NS" ping "$ping_flag" -Q 184 -c 25 -i 0.2 "$destination" >"$TMP_DIR/${policy}-v${family}-ping.txt" 2>&1 &
  local ping_pid=$!
  ip netns exec "$CLIENT_NS" iperf3 "-$family" -c "$destination" -p "$port" -t 8 -P 4 >"$TMP_DIR/${policy}-v${family}-bulk.txt" 2>&1 &
  local bulk_pid=$!
  ip netns exec "$CLIENT_NS" iperf3 "-$family" -c "$destination" -p "$rtport" -u -b 2M -S 184 -t 8 >"$TMP_DIR/${policy}-v${family}-media.txt" 2>&1 &
  local media_pid=$!
  wait "$bulk_pid" || true
  wait "$media_pid" || true
  wait "$ping_pid" || true
  cat "$TMP_DIR/${policy}-v${family}-ping.txt"
  echo "TCP bulk results:"
  tail -n 5 "$TMP_DIR/${policy}-v${family}-bulk.txt"
  echo "DSCP-marked UDP media results (includes loss and jitter):"
  tail -n 8 "$TMP_DIR/${policy}-v${family}-media.txt"
  echo "tc counters on the isolated client interface:"
  tc -s qdisc show dev eth0
}

echo "Isolated Linux kernel packet lab; no physical or home-network interface is used."
echo "Saving terminal evidence to: $REPORT_FILE"
run_case baseline 4 192.0.2.2 5201 5203 -4
run_case adaptive 4 192.0.2.2 5201 5203 -4
run_case baseline 6 2001:db8:201::2 5202 5204 -6
run_case adaptive 6 2001:db8:201::2 5202 5204 -6
echo
echo "Lab complete. The namespaces and their qdiscs will now be removed."
