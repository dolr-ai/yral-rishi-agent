#!/bin/sh
# garage-to-storj: nightly copy of every Garage bucket into one Storj bucket,
# the only copy of our data outside our own servers (Rishi, 2026-10-05: "one
# S3 = Garage, with a backup of it in Storj for just-in-case").
#
#   Garage <bucket>/<path>  ->  Storj yral-garage-backup/<bucket>/<path>
#
# `sync` makes Storj match Garage, but anything it would overwrite or delete
# is moved to yral-garage-backup-history/<date>/ first, so a mistake in
# Garage (a deleted file, a bad overwrite) is still recoverable from Storj
# for HISTORY_DAYS. Buckets are discovered each run: a new bucket is mirrored
# once the storj-mirror key may read it (`garage bucket allow --read`).
set -u
{ read -r GID; read -r GSEC; } < /run/secrets/garage_read_key
{ read -r SID; read -r SSEC; } < /run/secrets/storj_key
export RCLONE_CONFIG_G_TYPE=s3 RCLONE_CONFIG_G_PROVIDER=Other RCLONE_CONFIG_G_ENDPOINT=http://garage:3900 \
       RCLONE_CONFIG_G_REGION=garage RCLONE_CONFIG_G_FORCE_PATH_STYLE=true \
       RCLONE_CONFIG_G_ACCESS_KEY_ID="$GID" RCLONE_CONFIG_G_SECRET_ACCESS_KEY="$GSEC" \
       RCLONE_CONFIG_S_TYPE=s3 RCLONE_CONFIG_S_PROVIDER=Storj RCLONE_CONFIG_S_ENDPOINT=https://gateway.storjshare.io \
       RCLONE_CONFIG_S_ACCESS_KEY_ID="$SID" RCLONE_CONFIG_S_SECRET_ACCESS_KEY="$SSEC"
day=$(date -u +%Y-%m-%d)
failed=""
for b in $(rclone lsf g: | tr -d /); do
  if rclone sync "g:$b" "s:yral-garage-backup/$b" --backup-dir "s:yral-garage-backup-history/$day/$b" \
       --s3-no-check-bucket --transfers 16 --checkers 32 --fast-list -q; then
    echo "$b: ok ($(rclone size "s:yral-garage-backup/$b" --json | sed -E 's/.*"bytes":([0-9]+).*/\1/') bytes)"
  else
    failed="$failed $b"; echo "$b: FAILED"
  fi
done
rclone delete s:yral-garage-backup-history --min-age "${HISTORY_DAYS}d" --rmdirs -q || true

if [ -n "$failed" ]; then
  text="⚠️ Nightly Garage → Storj backup failed for:$failed"
  printf '{"text": "%s"}' "$text" | wget -q -O- --header 'Content-Type: application/json' --post-file=- "$(cat /run/secrets/gchat_webhook)" >/dev/null 2>&1
  exit 1
fi
