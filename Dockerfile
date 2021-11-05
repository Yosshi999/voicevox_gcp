# syntax=docker/dockerfile:1.3-labs

ARG BASE_IMAGE=python:3.7.12-bullseye
ARG BASE_RUNTIME_IMAGE=python:3.7.12-slim-bullseye

# Download VOICEVOX Core shared object
FROM ${BASE_IMAGE} AS download-core-env
WORKDIR /work

ARG VOICEVOX_CORE_VERSION=0.7.0
ARG VOICEVOX_CORE_LIBRARY_NAME=core_cpu
RUN <<EOF
    wget -nv --show-progress -c -O "./core.zip" "https://github.com/Hiroshiba/voicevox_core/releases/download/${VOICEVOX_CORE_VERSION}/core.zip"
    unzip "./core.zip"
    mv ./core /opt/voicevox_core
    rm ./core.zip
EOF

RUN <<EOF
    # Workaround: remove unused libcore (cpu, gpu)
    # Prevent error: `/sbin/ldconfig.real: /opt/voicevox_core/libcore.so is not a symbolic link`
    set -eux
    if [ "${VOICEVOX_CORE_LIBRARY_NAME}" = "core" ]; then
        rm -f /opt/voicevox_core/libcore_cpu.so
    elif [ "${VOICEVOX_CORE_LIBRARY_NAME}" = "core_cpu" ]; then
        mv /opt/voicevox_core/libcore_cpu.so /opt/voicevox_core/libcore.so
    else
        echo "Invalid VOICEVOX CORE library name: ${VOICEVOX_CORE_LIBRARY_NAME}" >> /dev/stderr
        exit 1
    fi
EOF

RUN <<EOF
    echo "/opt/voicevox_core" > /etc/ld.so.conf.d/voicevox_core.conf
    rm -f /etc/ld.so.cache
    ldconfig
EOF


# Download LibTorch
FROM ${BASE_IMAGE} AS download-libtorch-env
WORKDIR /work

ARG LIBTORCH_URL=https://download.pytorch.org/libtorch/cpu/libtorch-cxx11-abi-shared-with-deps-1.9.0%2Bcpu.zip
RUN <<EOF
    wget -nv --show-progress -c -O "./libtorch.zip" "${LIBTORCH_URL}"
    unzip "./libtorch.zip"
    mkdir -p /opt/libtorch
    mv ./libtorch/lib/*.so /opt/libtorch
    mv ./libtorch/lib/*.so.* /opt/libtorch
    rm ./libtorch.zip
EOF

RUN <<EOF
    LIBTORCH_PATH="/opt/libtorch"
    echo "${LIBTORCH_PATH}" > /etc/ld.so.conf.d/libtorch.conf
    rm -f /etc/ld.so.cache
    ldconfig
EOF


# Download VOICEVOX Engine
FROM ${BASE_IMAGE} AS download-engine-env

RUN <<EOF
    apt-get update
    apt-get install -y cmake mecab libmecab-dev
    apt-get clean
    rm -rf /var/lib/apt/lists/*
EOF

ARG VOCIEVOX_ENGINE_VERSION=0.7.0
RUN git clone -b "${VOCIEVOX_ENGINE_VERSION}" --depth 1 https://github.com/Hiroshiba/voicevox_engine.git /opt/voicevox_engine
WORKDIR /opt/voicevox_engine
RUN pip3 install -r requirements.txt

COPY --from=download-core-env /etc/ld.so.conf.d/voicevox_core.conf /etc/ld.so.conf.d/voicevox_core.conf
COPY --from=download-core-env /opt/voicevox_core /opt/voicevox_core
# Clone VOICEVOX Core example
ARG VOICEVOX_CORE_EXAMPLE_VERSION=0.7.0
RUN <<EOF
    git clone -b "${VOICEVOX_CORE_EXAMPLE_VERSION}" --depth 1 https://github.com/Hiroshiba/voicevox_core.git /opt/voicevox_core_example
    cd /opt/voicevox_core_example/example/python
    cp /opt/voicevox_core/core.h .
    LIBRARY_PATH="$LIBRARY_PATH:/opt/voicevox_core" pip3 install .
EOF

RUN <<EOF
    wget -nv --show-progress -c -O "./additional_openjtalk_dic.zip" "https://github.com/takana-v/additional_openjtalk_dic/releases/download/0.0.1/additional_openjtalk_dic.zip"
    unzip "./additional_openjtalk_dic.zip"
    mkdir -p /opt/voicevox_engine/tdmelodic
    mv ./additional_openjtalk_dic/additional_openjtalk_dic.csv /usr/share/mecab/dic/juman/* /opt/voicevox_engine/tdmelodic
    rm ./additional_openjtalk_dic.zip
    rm -r ./additional_openjtalk_dic
    cd /opt/voicevox_engine/tdmelodic
    /usr/lib/mecab/mecab-dict-index -f utf-8 -t utf-8
    rm *.csv
EOF

# Runtime
FROM ${BASE_RUNTIME_IMAGE} AS runtime-env
WORKDIR /opt/voicevox_engine

RUN <<EOF
    apt-get update
    apt-get install -y libsndfile1 gosu
    apt-get clean
    rm -rf /var/lib/apt/lists/*
EOF

COPY --from=download-core-env /etc/ld.so.conf.d/voicevox_core.conf /etc/ld.so.conf.d/voicevox_core.conf
COPY --from=download-core-env /opt/voicevox_core /opt/voicevox_core

COPY --from=download-libtorch-env /etc/ld.so.conf.d/libtorch.conf /etc/ld.so.conf.d/libtorch.conf
COPY --from=download-libtorch-env /opt/libtorch /opt/libtorch

COPY --from=download-engine-env /usr/local/lib/python3.7/site-packages /usr/local/lib/python3.7/site-packages 
COPY --from=download-engine-env /opt/voicevox_engine /opt/voicevox_engine

COPY ./run_container.py /opt/voicevox_engine/

COPY --chmod=775 ./entrypoint.sh /entrypoint.sh

RUN useradd --create-home user && ldconfig
ENTRYPOINT [ "/entrypoint.sh" ]
ENV PORT=50021
ENV OPEN_JTALK_DICT_DIR=/opt/voicevox_engine/tdmelodic
CMD [ "gosu", "user", "python3", "-B", "./run_container.py", "--voicevox_dir", "/opt/voicevox_core/", "--voicelib_dir", "/opt/voicevox_core/", "--host", "0.0.0.0" ]
