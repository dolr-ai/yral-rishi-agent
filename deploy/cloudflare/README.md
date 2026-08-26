# cdn-yral-sfw worker

Serves the hostname the mobile app builds video URLs against.

## Why this exists

The app never asks a service where a video lives — it constructs the URL:

```
https://cdn-yral-sfw.yral.com/{oauth_subject}/{video_id}.mp4
https://cdn-yral-sfw.yral.com/{oauth_subject}/{video_id}-thumbnail.png
```

Videos from the new agent service go to the **Storj** bucket `yral-videos`.
Everything older is in the **Hetzner** bucket this hostname has always served.

We cannot verify the old catalogue is mirrored into Storj — our Storj account
returns `NoSuchBucket` for `yral-sfw`, so it is a different account from the one
the departed service used. Repointing the hostname wholesale would therefore risk
breaking every existing video at once.

So the worker tries Storj first and falls back to Hetzner. New videos play, old
videos are untouched, and no DNS or mobile change is needed. When the old
content is gone or mirrored, delete the fallback and the worker becomes a
straight Storj proxy.

## How it reads Storj

A signed (SigV4) request, so the bucket stays private — no public link-share.
Range requests pass through unchanged, which is what makes video seeking work:
the player asks for byte ranges and gets `206`s back.

The signing was verified against the live gateway before this worker was
written — a hand-rolled SigV4 request using the same algorithm returned
`HTTP 206, video/mp4`.

## Deploy

```sh
cd deploy/cloudflare
wrangler secret put STORJ_ACCESS_KEY_ID
wrangler secret put STORJ_SECRET_ACCESS_KEY
wrangler deploy
```

Credentials are the same Storj gateway pair the agent service writes with; the
worker only reads.

## Rolling back

Delete the worker route. The hostname reverts to its Hetzner origin and old
videos keep serving exactly as before — only videos created after the agent
service took over would stop resolving.
