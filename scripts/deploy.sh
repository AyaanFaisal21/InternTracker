#!/bin/sh -e
# Prod deploy, run on the box. CI triggers it over ssh via a forced-command
# key, so this script is the only thing that key can execute.
cd /opt/ruemployed
git pull --ff-only
sudo docker compose build
sudo docker compose up -d
sudo docker image prune -f >/dev/null
echo "deployed $(git rev-parse --short HEAD)"
