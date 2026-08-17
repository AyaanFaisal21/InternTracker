#!/bin/sh -e
# Prod deploy, run on the box. CI triggers it over ssh via a forced-command
# key, so this script is the only thing that key can execute.
cd /opt/ruemployed
git pull --ff-only
sudo docker compose build
# `frontend` builds the static bundle and exits. Compose renames the exited
# container while recreating it, and the old name survives to collide with
# the next deploy, which then fails. Clear it first: it holds no state.
sudo docker compose rm -fs frontend >/dev/null 2>&1 || true
sudo docker compose up -d
sudo docker image prune -f >/dev/null
echo "deployed $(git rev-parse --short HEAD)"
