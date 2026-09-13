#!/usr/bin/env bash
# Stand up the content generator on a Debian/Ubuntu VPS.
#
# Generation moved off GitHub Actions on 2026-09-11 because it runs on a local
# model now and a hosted runner cannot reach one. That put the nightly job on
# the owner's PC, which is fine until the PC is off or travelling. This script
# puts the same job on a box that is never off.
#
# Nothing about the pipeline changes: same repo, same accounts, same code path.
# Only the scheduler differs — Windows Task Scheduler there, a systemd timer
# here — because everything else was already portable.
#
#   git clone https://github.com/vbolinches/leverage-ai-content.git
#   cd leverage-ai-content
#   sudo bash deploy/vps-setup.sh            # installs, then tells you what is left
#   python3 run_local_batch.py --check       # must print "ready" before you trust it
#
# Idempotent: safe to re-run after changing the model or the hour.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_AS="${SUDO_USER:-$(id -un)}"
MODEL="${OLLAMA_MODEL:-qwen3:30b-a3b}"
AT="${BATCH_AT:-03:00}"

say() { printf '\n=== %s\n' "$*"; }

say "System packages"
# curl is not optional: uscis.gov and dhs.gov refuse Python's urllib on the TLS
# handshake and serve curl fine, and they are half of one account's allowed
# sources. fonts-dejavu-core is what keeps slides from rendering as boxes.
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl fonts-dejavu-core

say "Python dependencies"
sudo -u "$RUN_AS" python3 -m pip install --quiet --upgrade --user \
    pillow numpy imageio-ffmpeg ddgs

say "Ollama"
if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
systemctl enable --now ollama

say "Model: $MODEL"
# On a CPU-only VPS this is the slow part and the expensive one. See the
# sizing note in README before assuming the box is big enough.
sudo -u "$RUN_AS" ollama pull "$MODEL"

say "Nightly timer at $AT"
cat >/etc/systemd/system/leverage-batch.service <<EOF
[Unit]
Description=Leverage AI nightly content batch
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_AS
WorkingDirectory=$REPO
Environment=OLLAMA_MODEL=$MODEL
Environment=PYTHONIOENCODING=utf-8
ExecStart=/usr/bin/python3 -u $REPO/run_local_batch.py
# A 7-post batch is roughly 40 minutes on a GPU and several hours on CPU, so
# this is generous on purpose. It is a ceiling against a hang, not a budget.
TimeoutStartSec=6h

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/leverage-batch.timer <<EOF
[Unit]
Description=Run the Leverage AI content batch nightly

[Timer]
OnCalendar=*-*-* $AT:00
# The equivalent of Task Scheduler's "run if a run was missed": a reboot or a
# host outage must not silently cost a night's generation.
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now leverage-batch.timer

say "Installed"
systemctl list-timers leverage-batch.timer --no-pager || true
cat <<EOF

Two things this script cannot do for you:

 1. Push credentials. The batch commits and pushes, so this host needs write
    access to the repo. Use a deploy key with write access, or a fine-grained
    PAT in a credential helper — not your personal password. Verify with:
        sudo -u $RUN_AS git -C $REPO ls-remote origin

 2. Decide whether this box is big enough. Check it honestly:
        sudo -u $RUN_AS python3 $REPO/run_local_batch.py --check
        sudo -u $RUN_AS python3 $REPO/run_local_batch.py --dry-run --force --count 2

    The dry run is the real test. It generates without queueing anything, so
    you find out how long a batch takes here before a schedule depends on it.

Run one now:   sudo systemctl start leverage-batch.service
Watch it:      journalctl -u leverage-batch -f
Logs:          $REPO/logs/batch-<date>.log
EOF
