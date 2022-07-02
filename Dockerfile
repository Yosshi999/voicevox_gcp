# syntax=docker/dockerfile:1.3-labs

ARG BASE_IMAGE=python:3.8.13-bullseye
ARG BASE_RUNTIME_IMAGE=python:3.8.13-slim-bullseye

# Download VOICEVOX Core shared object
FROM ${BASE_IMAGE} AS download-core-env
WORKDIR /work

ARG VOICEVOX_CORE_ASSET_NAME=voicevox_core-linux-x64-cpu-0.12.3
ARG VOICEVOX_CORE_VERSION=0.12.3
RUN <<EOF
    set -eux
    wget -nv --show-progress -c -O "./${VOICEVOX_CORE_ASSET_NAME}.zip" "https://github.com/VOICEVOX/voicevox_core/releases/download/${VOICEVOX_CORE_VERSION}/${VOICEVOX_CORE_ASSET_NAME}.zip"
    unzip "./${VOICEVOX_CORE_ASSET_NAME}.zip"
    mkdir /opt/voicevox_core
    mv "${VOICEVOX_CORE_ASSET_NAME}/libcore.so" /opt/voicevox_core/
    mv "${VOICEVOX_CORE_ASSET_NAME}/VERSION" /opt/voicevox_core/
    rm -rf $VOICEVOX_CORE_ASSET_NAME
    rm "./${VOICEVOX_CORE_ASSET_NAME}.zip"

    echo "/opt/voicevox_core" > /etc/ld.so.conf.d/voicevox_core.conf
    rm -f /etc/ld.so.cache
    ldconfig
EOF


# Download ONNX Runtime
FROM ${BASE_IMAGE} AS download-onnxruntime-env
WORKDIR /work

ARG ONNXRUNTIME_URL=https://github.com/microsoft/onnxruntime/releases/download/v1.10.0/onnxruntime-linux-x64-1.10.0.tgz
RUN <<EOF
    set -eux

    # Download ONNX Runtime
    wget -nv --show-progress -c -O "./onnxruntime.tgz" "${ONNXRUNTIME_URL}"

    # Extract ONNX Runtime to /opt/onnxruntime
    mkdir -p /opt/onnxruntime
    tar xf "./onnxruntime.tgz" -C "/opt/onnxruntime" --strip-components 1
    rm ./onnxruntime.tgz

    # Add /opt/onnxruntime/lib to dynamic library search path
    echo "/opt/onnxruntime/lib" > /etc/ld.so.conf.d/onnxruntime.conf

    # Update dynamic library search cache
    ldconfig
EOF


# Download VOICEVOX Engine
FROM ${BASE_IMAGE} AS download-engine-env

RUN <<EOF
    apt-get update
    apt-get install -y cmake
    apt-get clean
    rm -rf /var/lib/apt/lists/*
EOF

ARG VOCIEVOX_ENGINE_VERSION=0.12.2
RUN git clone -b "${VOCIEVOX_ENGINE_VERSION}" --depth 1 https://github.com/VOICEVOX/voicevox_engine.git /opt/voicevox_engine
WORKDIR /opt/voicevox_engine
RUN pip3 install -r requirements.txt

# execute lazy_init and download mecab dic
RUN python3 -c "import pyopenjtalk;print(pyopenjtalk.g2p('ハローワールド'))"

RUN <<EOF
    set -eux

    mkdir /opt/dic
    wget -O "additional_openjtalk_dic.zip" https://github.com/takana-v/additional_openjtalk_dic/releases/download/0.0.1/additional_openjtalk_dic.zip
    unzip additional_openjtalk_dic.zip
    cat additional_openjtalk_dic/additional_openjtalk_dic.csv >> default.csv
    python -c "import pyopenjtalk;pyopenjtalk.create_user_dict('default.csv', 'user.dic')"
    mv user.dic /opt/dic/user.dic
    rm -rf additional_openjtalk_dic
    rm additional_openjtalk_dic.zip
    rm default.csv
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

# COPY --from=download-core-env /etc/ld.so.conf.d/voicevox_core.conf /etc/ld.so.conf.d/voicevox_core.conf
COPY --from=download-core-env /opt/voicevox_core /opt/voicevox_core

# COPY --from=download-onnxruntime-env /etc/ld.so.conf.d/onnxruntime.conf /etc/ld.so.conf.d/onnxruntime.conf
COPY --from=download-onnxruntime-env /opt/onnxruntime /opt/onnxruntime

COPY --from=download-engine-env /usr/local/lib/python3.8/site-packages /usr/local/lib/python3.8/site-packages 
COPY --from=download-engine-env /opt/voicevox_engine /opt/voicevox_engine

COPY ./run_container.py /opt/voicevox_engine/

COPY --chmod=775 ./entrypoint.sh /entrypoint.sh

RUN useradd --create-home user && ldconfig
COPY --from=download-engine-env /opt/dic/user.dic /home/user/.local/share/voicevox-engine/user.dic
ENTRYPOINT [ "/entrypoint.sh" ]
ENV PORT=50021
CMD [ "gosu", "user", "python3", "-B", "./run_container.py", "--voicelib_dir", "/opt/voicevox_core/", "--runtime_dir", "/opt/onnxruntime/lib", "--host", "0.0.0.0" ]
