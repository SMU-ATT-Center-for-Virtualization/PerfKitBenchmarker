FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
  gcc \
  make \
  curl \
  python3-pip \
  python3-dev \
  librrd-dev \
  texinfo \
  build-essential \
  git \
  libtool \
  autoconf \
  automake \
  patch \
  lksctp-tools \
  libsctp-dev

RUN pip3 install absl-py six rrdtool

# get netperf source
RUN curl -LO https://github.com/HewlettPackard/netperf/archive/netperf-2.7.0.tar.gz && \
  tar -xzf netperf-2.7.0.tar.gz

# get netperf patch (histogram scaling and demo scripts)
RUN curl -LO https://raw.githubusercontent.com/GoogleCloudPlatform/PerfKitBenchmarker/master/perfkitbenchmarker/data/netperf.patch

# compile / install netperf
RUN cd netperf-netperf-2.7.0 && \
  patch -l -p1 < ../netperf.patch && \
    CFLAGS="-DHIST_NUM_OF_BUCKET=1000" ./configure --enable-burst --enable-demo --enable-histogram --enable-sctp && \
    make && make install

# expose control/data ports for netperf/netserver instances
# NOTE: currently hard-coded to accomadate up to 32 parallel streams
# will need to modify for benchmark runs which utilize >32 parallel streams
EXPOSE 20000-20064

# startup netserver instances and sleep infinity to keep container alive
CMD ["/bin/bash", "-c", "for i in $(seq 20000 2 20064); do /usr/local/bin/netserver -p $i; done && sleep infinity"]
