#!/bin/sh
# edge-cert-sync: copy the certificates edge-issuer keeps in Redis into the
# serving edge's secrets, then roll the edge onto them with no downtime.
#
# Secrets are named by a hash of the certificate, so an unchanged certificate
# is skipped and only a real renewal (or a new host) causes a rolling update.
# Every host the issuer holds a certificate for is synced; adding a host is a
# change to issuer.Caddyfile (to get the certificate) and serve.Caddyfile (to
# use it), nothing here.
#
# Runs inside the edge-cert-sync service (see edge-cert-sync-stack.yml), which
# repeats it every SYNC_EVERY seconds. Until 2026-10-04 this was a cron job on
# rishi-4, which stopped working when rishi-4 left the swarm it managed.
set -eu
SVC=yral-v2-edge-caddy_caddy-edge-ingress
RP=$(cat /run/secrets/redis_password)
redis() { redis-cli -h redis-primary -a "$RP" --no-auth-warning "$@"; }

# Caddy stores each file as {"value": "<base64>"}.
redis_pem() { redis GET "$1" | jq -r .value | base64 -d; }

# The secret currently mounted at a target filename, or nothing.
mounted_secret() {
  docker service inspect "$SVC" \
    --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{.SecretName}} {{.File.Name}}
{{end}}' | awk -v t="$1" '$2==t{print $1}'
}

changes=""
for crt_key in $(redis --scan --pattern 'caddy_issuer/certificates/*/*/*.crt'); do
  host=$(basename "$crt_key" .crt)
  key_key="${crt_key%.crt}.key"
  crt_pem=$(redis_pem "$crt_key")
  hash=$(printf '%s' "$crt_pem" | sha256sum | cut -c1-12)
  name=$(echo "$host" | tr '.' '_')
  new_crt="sync_${name}_crt_${hash}"
  new_key="sync_${name}_key_${hash}"
  old_crt=$(mounted_secret "$host.crt")
  old_key=$(mounted_secret "$host.key")
  if [ "$old_crt" = "$new_crt" ]; then echo "$host: unchanged ($hash)"; continue; fi
  printf '%s' "$crt_pem" | docker secret create "$new_crt" - >/dev/null 2>&1 || true
  redis_pem "$key_key" | docker secret create "$new_key" - >/dev/null 2>&1 || true
  # A host the edge has never served has nothing to remove.
  [ -n "$old_crt" ] && changes="$changes --secret-rm $old_crt"
  [ -n "$old_key" ] && changes="$changes --secret-rm $old_key"
  changes="$changes --secret-add source=$new_crt,target=$host.crt --secret-add source=$new_key,target=$host.key"
  echo "$host: CHANGED -> $hash"
done

if [ -n "$changes" ]; then
  # stop-first, one at a time: the edge allows one copy per server, so with
  # every server already holding one, start-first has nowhere to put the new
  # copy and the update hangs (seen 2026-10-04). The edge publishes through
  # the swarm's routing mesh, so the other copies serve while one restarts.
  # shellcheck disable=SC2086
  docker service update $changes --update-order stop-first --update-parallelism 1 --detach "$SVC" >/dev/null
  echo "edge rolling onto new certificates"
else
  echo "nothing to update"
fi
