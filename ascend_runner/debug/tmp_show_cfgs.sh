#!/usr/bin/env bash
set -e
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
echo ===ROOT_CFG===
ls -l "$APPDIR/1982_cloud_config.toml"
sed -n '1,80p' "$APPDIR/1982_cloud_config.toml"
echo ===ETC_CFG===
ls -l "$APPDIR/etc/1982_cloud_config.toml"
sed -n '1,80p' "$APPDIR/etc/1982_cloud_config.toml"