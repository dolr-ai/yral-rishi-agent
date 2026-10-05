#!/bin/sh
# fleet-monitor: one hourly health check of the whole swarm, posted to Google
# Chat when something is wrong, plus one "all OK" post a day so silence never
# means the monitor itself died.
#
# Replaces four cron jobs that lived on Hetzner servers until 2026-10-05
# (backup-monitor, edge-cert-monitor, monday-cluster-check, edge-cert-sync's
# own logging). Everything is checked over the network, so it runs on any
# manager and moves if that manager dies.
set -u
CHAT=$(cat /run/secrets/gchat_webhook)
{ read -r GID; read -r GSEC; } < /run/secrets/garage_read_key
problems=""
add() { problems="${problems}
• $1"; }

# Database: a leader, a sync standby (no acknowledged write is ever on one
# copy only), every member running, nobody far behind.
cluster=$(curl -s --max-time 10 http://patroni:8008/cluster)
if [ -z "$cluster" ]; then add "database: Patroni status unreachable"; else
  echo "$cluster" | jq -e '.members[] | select(.role=="leader")' >/dev/null || add "database: NO leader"
  echo "$cluster" | jq -e '.members[] | select(.role=="sync_standby")' >/dev/null || add "database: no sync standby (writes not protected by a second copy)"
  bad=$(echo "$cluster" | jq -r '.members[] | select(.state!="running" and .state!="streaming") | "\(.name)=\(.state)"' | tr '\n' ' ')
  [ -n "$bad" ] && add "database members not running: $bad"
  lag=$(echo "$cluster" | jq '[.members[].lag // 0 | numbers] | max // 0')
  [ "$lag" -gt 524288000 ] && add "database: a copy is $((lag / 1048576)) MB behind"
  [ "$(echo "$cluster" | jq '.members | length')" -lt 3 ] && add "database: fewer than 3 copies"
fi

# Backups: a full backup must land in Garage every day. It can't complete
# unless WAL archiving works, so this covers both. (Archiving itself is not
# timed: an idle database rightly archives nothing.)
newest=$(AWS_ACCESS_KEY_ID=$GID AWS_SECRET_ACCESS_KEY=$GSEC aws --endpoint-url http://garage:3900 --region garage \
  s3 ls s3://postgres-backups/yral-rishi-agent-walg/basebackups_005/ 2>/dev/null \
  | grep backup_stop_sentinel | sort | tail -1 | awk '{print $1" "$2}')
if [ -z "$newest" ]; then add "backups: no full backup found in Garage"; else
  age_h=$(( ( $(date -u +%s) - $(date -u -d "$newest" +%s) ) / 3600 ))
  [ "$age_h" -gt 26 ] && add "backups: newest full backup is ${age_h}h old (limit 26h)"
fi

# Storage, services, servers.
[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://garage:3903/health)" = 200 ] || add "Garage (our S3) reports unhealthy"
# Sentry runs outside the swarm (docker compose on one server); its nginx keeps
# the fixed address 10.0.1.11 on yral-v2-public-web, which the edge uses too.
[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://10.0.1.11/_health/)" = 200 ] || add "Sentry is not answering (it runs outside the swarm: see yral-rishi-sentry RUNBOOK §10)"
short=$(docker service ls --format '{{.Name}} {{.Replicas}}' | awk '{split($2,a,"/"); if (a[1]!=a[2]) printf "%s(%s) ", $1, $2}')
[ -n "$short" ] && add "services below their copies: $short"
down=$(docker node ls --format '{{.Hostname}} {{.Status}} {{.Availability}}' | awk '$3=="Active" && $2!="Ready"{printf "%s ", $1}')
[ -n "$down" ] && add "servers down: $down"

# Certificates: every certificate the edge serves, warned 14 days early
# (renewal happens ~30 days before expiry, so this means renewal is stuck).
for f in $(docker service inspect yral-v2-edge-caddy_caddy-edge-ingress --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{.File.Name}} {{end}}' | tr ' ' '\n' | grep '\.crt$'); do
  host=${f%.crt}
  end=$(echo | timeout 10 openssl s_client -connect caddy-edge-ingress:443 -servername "$host" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  if [ -z "$end" ]; then add "certificate for $host: could not read"; continue; fi
  days=$(( ( $(date -u -d "$end" +%s) - $(date -u +%s) ) / 86400 ))
  [ "$days" -lt 14 ] && add "certificate for $host expires in ${days} days"
done

post() { jq -n --arg t "$1" '{text: $t}' | curl -s --max-time 10 -H 'Content-Type: application/json' -d @- "$CHAT" >/dev/null; }
if [ -n "$problems" ]; then
  echo "PROBLEMS:$problems"; post "⚠️ Fleet check found problems:$problems"
else
  echo "all OK"
  # One daily heartbeat, in the 03:00 UTC run (08:30 IST).
  if [ "$(date -u +%H)" = "03" ]; then
    post "✅ Daily fleet check: database, backups, storage, services, servers and certificates all OK."
  fi
fi
