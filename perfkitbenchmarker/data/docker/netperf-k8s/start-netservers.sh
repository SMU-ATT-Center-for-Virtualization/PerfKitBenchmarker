#!/bin/bash
# Copyright 2026 PerfKitBenchmarker Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Starts one netserver per netperf stream and keeps the container alive.
set -euo pipefail

port_start="${NETPERF_PORT_START:-20200}"
max_streams="${NETPERF_MAX_STREAMS:-32}"

if ! [[ "${port_start}" =~ ^[0-9]+$ ]] || (( port_start < 1 )); then
  echo "Invalid NETPERF_PORT_START: ${port_start}" >&2
  exit 1
fi
if ! [[ "${max_streams}" =~ ^[0-9]+$ ]] || (( max_streams < 1 )); then
  echo "Invalid NETPERF_MAX_STREAMS: ${max_streams}" >&2
  exit 1
fi

for ((i = 0; i < max_streams; i++)); do
  port=$((port_start + (i * 2)))
  /usr/local/bin/netserver -p "${port}" \
    >"/tmp/netserver-${port}.log" 2>&1 &
done

exec sleep infinity
