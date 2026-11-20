FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ARG UCX_VERSION=v1.22.0
ARG NIXL_VERSION=v1.5.2
ARG NIXL_PY_VERSION=1.5.2

ENV PKG_CONFIG_PATH=/usr/local/ucx/lib/pkgconfig:/usr/local/nixl/lib/pkgconfig:/usr/local/nixl/lib/x86_64-linux-gnu/pkgconfig:/usr/local/nixl/lib/aarch64-linux-gnu/pkgconfig:$PKG_CONFIG_PATH \
    LD_LIBRARY_PATH=/usr/local/ucx/lib:/usr/local/nixl/lib:/usr/local/nixl/lib/x86_64-linux-gnu:/usr/local/nixl/lib/aarch64-linux-gnu:/usr/local/nixl/lib/plugins:/usr/local/nixl/lib/x86_64-linux-gnu/plugins:/usr/local/nixl/lib/aarch64-linux-gnu/plugins:/usr/local/nixlbench/lib:/usr/local/nixlbench/lib/x86_64-linux-gnu:/usr/local/nixlbench/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH \
    PATH=/usr/local/nixlbench/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
  autoconf \
  automake \
  build-essential \
  ca-certificates \
  cmake \
  curl \
  git \
  iproute2 \
  libaio-dev \
  libcurl4-openssl-dev \
  libgflags-dev \
  libibverbs-dev \
  libnuma-dev \
  librdmacm-dev \
  libssl-dev \
  libtool \
  liburing-dev \
  meson \
  netcat-openbsd \
  ninja-build \
  pkg-config \
  procps \
  pybind11-dev \
  python3 \
  python3-dev \
  python3-pip \
  python3-venv \
  rdma-core \
  tar \
  uuid-dev \
  zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/pkb \
  && git clone --depth 1 --branch "${UCX_VERSION}" \
    https://github.com/openucx/ucx.git /opt/pkb/ucx \
  && cd /opt/pkb/ucx \
  && ./autogen.sh \
  && ./contrib/configure-release \
    --prefix=/usr/local/ucx --enable-mt --with-verbs --without-cuda \
  && make -j"$(nproc)" \
  && make install \
  && ldconfig

RUN git clone --depth 1 --branch "${NIXL_VERSION}" \
    https://github.com/ai-dynamo/nixl.git /opt/pkb/nixl \
  && cd /opt/pkb/nixl \
  && meson setup build \
    --prefix=/usr/local/nixl \
    --buildtype=release \
    -Ducx_path=/usr/local/ucx \
    -Denable_plugins=UCX \
    -Ddisable_gds_backend=true \
    -Dbuild_tests=false \
    -Dbuild_examples=false \
  && ninja -C build \
  && ninja -C build install \
  && ldconfig

RUN cd /opt/pkb/nixl/benchmark/nixlbench \
  && meson setup build \
    --prefix=/usr/local/nixlbench \
    --buildtype=release \
    -Dnixl_path=/usr/local/nixl \
    -Ducx_path=/usr/local/ucx \
    -Dbuild_tests=false \
  && ninja -C build \
  && ninja -C build install \
  && ldconfig

RUN python3 -m venv /opt/pkb/kvbench-venv \
  && /opt/pkb/kvbench-venv/bin/pip install --upgrade pip setuptools wheel \
  && /opt/pkb/kvbench-venv/bin/pip install --no-deps \
    "nixl==${NIXL_PY_VERSION}" "nixl-cu12==${NIXL_PY_VERSION}" \
  && /opt/pkb/kvbench-venv/bin/pip install \
    'click==8.1.7' 'pyyaml>=6.0.2' 'etcd3>=0.12.0' \
    'tabulate==0.9.0' 'tqdm==4.66.5' 'numpy' \
  && /opt/pkb/kvbench-venv/bin/pip install --no-deps \
    -e /opt/pkb/nixl/benchmark/kvbench

RUN test -x /usr/local/nixlbench/bin/nixlbench \
  && test -f /opt/pkb/nixl/benchmark/kvbench/main.py

CMD ["sleep", "infinity"]
