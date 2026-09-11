# bootstrap-scripts-for-the-v2-docker-swarm-cluster

**Status:** empty placeholder. Cluster bootstrap scripts (Ansible/systemd/UFW/docker-swarm-init) go here when Phase 9 (deploy to rishi-4/5/6) gets explicit approval.

Includes:
- `cluster.hosts.yaml` (shape only — IPs from GitHub Secrets)
- `systemd/yral-v2-swarm-resync.service`
- `scripts/swarm-init.sh`, `apply-node-labels.sh`, `generate-caddy-snippets.sh`, etc.
- UFW firewall rules

The cluster it builds is described in `docs/runbooks/` and CLAUDE.md's Architecture section.
