# The tools the Linux jobs of .github/workflows/source-regressions.yml run with, as the ubuntu-24.04 runner has them.
# Built and driven by scripts/ci_local.py; nothing of the game is in it. glslang is the pinned release the workflow
# downloads (the checked-in shader headers are generated with it).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git g++ clang ccache cmake make ninja-build pkg-config spirv-tools libdav1d-dev \
      python3 python3-venv python3-pip python3-tk lld dotnet-sdk-8.0 \
      mesa-vulkan-drivers vulkan-validationlayers libvulkan-dev vulkan-tools \
    && rm -rf /var/lib/apt/lists/*
ARG GLSLANG_VERSION=16.6.0
ARG GLSLANG_SHA256=2c34071f56ecf39d16233294d3739cf43f981fd39080f2fd9dccb782101191ce
RUN mkdir -p /opt/glslang && curl -fL --retry 3 -o /tmp/glslang.tar.gz \
      https://github.com/KhronosGroup/glslang/releases/download/${GLSLANG_VERSION}/glslang-${GLSLANG_VERSION}-linux-x86_64-release.tar.gz \
    && echo "${GLSLANG_SHA256}  /tmp/glslang.tar.gz" | sha256sum --check --strict \
    && tar -xzf /tmp/glslang.tar.gz -C /opt/glslang && rm /tmp/glslang.tar.gz
ARG ACTIONLINT_VERSION=1.7.12
ARG ACTIONLINT_SHA256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
RUN curl -fsSL --retry 5 -o /tmp/al.tar.gz https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz \
    && echo "${ACTIONLINT_SHA256}  /tmp/al.tar.gz" | sha256sum -c - && tar -xzf /tmp/al.tar.gz -C /usr/local/bin actionlint && rm /tmp/al.tar.gz
# (the packages tests import, as the workflow's "Python packages for the tests" step pins them)
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check --no-deps \
      zstandard==0.25.0 pillow==13.0.0 numpy==2.5.4 lz4==4.4.5 unicorn==2.1.4
ENV PATH=/opt/venv/bin:/opt/glslang/bin:$PATH CCACHE_DIR=/ccache CCACHE_MAXSIZE=400M
WORKDIR /work
