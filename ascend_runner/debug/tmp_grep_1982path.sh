#!/usr/bin/env bash
set -e
ROOT=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510
grep -R -a -n '\./etc/1982_cloud_config.toml\|1982_cloud_config.toml' "$ROOT" 2>/dev/null | sed -n '1,120p' || true