FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    NETPERF_PORT_START=20200 \
    NETPERF_MAX_STREAMS=32

RUN apt-get update && apt-get install -y --no-install-recommends \
  autoconf \
  automake \
  build-essential \
  ca-certificates \
  curl \
  g++ \
  gcc \
  libsctp-dev \
  librrd-dev \
  libtool \
  lksctp-tools \
  make \
  patch \
  pkg-config \
  python3 \
  python3-dev \
  python3-pip \
  texinfo \
  && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 marks the system Python as externally managed (PEP 668).
RUN pip3 install --break-system-packages --no-cache-dir \
  absl-py \
  rrdtool \
  six

RUN curl -LO https://github.com/HewlettPackard/netperf/archive/netperf-2.7.0.tar.gz \
  && tar -xzf netperf-2.7.0.tar.gz \
  && rm netperf-2.7.0.tar.gz

# Keep this patched copy in sync with perfkitbenchmarker/data/netperf.patch.
COPY netperf.patch /tmp/netperf.patch

RUN cd netperf-netperf-2.7.0 \
  && patch -l -p1 < /tmp/netperf.patch \
  && CFLAGS="-fcommon -std=gnu17 -Wno-error=implicit-function-declaration \
-Wno-error=incompatible-pointer-types -DHIST_NUM_OF_BUCKET=1000" \
  ./configure \
    --enable-burst \
    --enable-demo \
    --enable-histogram \
    --enable-intervals \
    --enable-sctp \
  && make -j"$(nproc)" \
  && make install

WORKDIR /
# RunNetperf expects the source-tree binary at the standard PKB install path.
RUN mkdir -p /opt/pkb \
  && ln -s /netperf-netperf-2.7.0 /opt/pkb/netperf-netperf-2.7.0

COPY start-netservers.sh /usr/local/bin/start-netservers.sh
RUN chmod +x /usr/local/bin/start-netservers.sh

CMD ["/usr/local/bin/start-netservers.sh"]
