# syntax=docker/dockerfile:1.3-labs

ARG BASE_IMAGE=python:3.8.12-bullseye
ARG BASE_RUNTIME_IMAGE=python:3.8.12-slim-bullseye

# Download VOICEVOX Core shared object
FROM ${BASE_IMAGE} AS download-core-env
WORKDIR /work

ARG VOICEVOX_CORE_VERSION=0.11.1
ARG VOICEVOX_CORE_LIBRARY_NAME=libcore_cpu_x64.so
RUN <<EOF
    wget -nv --show-progress -c -O "./core.zip" "https://github.com/VOICEVOX/voicevox_core/releases/download/${VOICEVOX_CORE_VERSION}/core.zip"
    unzip "./core.zip"
    mv ./core /opt/voicevox_core
    # mv "/opt/voicevox_core/${VOICEVOX_CORE_LIBRARY_NAME}" /opt/voicevox_core/libcore.so
    rm ./core.zip
EOF


# Download ONNX Runtime
FROM ${BASE_IMAGE} AS download-onnxruntime-env
WORKDIR /work

ARG ONNXRUNTIME_URL=https://github.com/microsoft/onnxruntime/releases/download/v1.10.0/onnxruntime-linux-x64-1.10.0.tgz
RUN <<EOF
    set -eux
    wget -nv --show-progress -c -O "./onnxruntime.tgz" "${ONNXRUNTIME_URL}"
    mkdir -p /opt/onnxruntime
    tar xf "./onnxruntime.tgz" -C "/opt/onnxruntime" --strip-components 1
    rm ./onnxruntime.tgz
EOF


# Download VOICEVOX Engine
FROM ${BASE_IMAGE} AS download-engine-env

RUN <<EOF
    apt-get update
    apt-get install -y cmake
    apt-get clean
    rm -rf /var/lib/apt/lists/*
EOF

WORKDIR /opt/dic
RUN <<EOF
    set -e
    git clone --recursive -b PR-user-dic https://github.com/Yosshi999/pyopenjtalk.git
    cd pyopenjtalk
    pip install .
    cd /opt/dic
    wget -O "additional_openjtalk_dic.zip" https://github.com/takana-v/additional_openjtalk_dic/releases/download/0.0.1/additional_openjtalk_dic.zip
    unzip additional_openjtalk_dic.zip
    python -c "import pyopenjtalk;pyopenjtalk.create_user_dict('additional_openjtalk_dic/additional_openjtalk_dic.csv', 'user.dic')"
    mkdir -p /opt/voicevox_dictionary
    mv user.dic /opt/voicevox_dictionary
    rm -rf ./*
EOF

ARG VOCIEVOX_ENGINE_VERSION=0.11.3
RUN git clone -b "${VOCIEVOX_ENGINE_VERSION}" --depth 1 https://github.com/VOICEVOX/voicevox_engine.git /opt/voicevox_engine
WORKDIR /opt/voicevox_engine
RUN sed -i -e '/pyopenjtalk/d' requirements.txt && pip3 install -r requirements.txt

# COPY --from=download-core-env /opt/voicevox_core /opt/voicevox_core
# COPY --from=download-onnxruntime-env /opt/onnxruntime/lib /opt/onnxruntime/lib

# # Clone VOICEVOX Core example
# ARG VOICEVOX_CORE_EXAMPLE_VERSION=0.11.1
# RUN <<EOF
#     git clone -b "${VOICEVOX_CORE_EXAMPLE_VERSION}" --depth 1 https://github.com/VOICEVOX/voicevox_core.git /opt/voicevox_core_example
#     mkdir -p /opt/voicevox_core_example/core/lib
#     cd /opt/voicevox_core_example
#     cp /opt/voicevox_core/core.h ./core/lib
#     cp /opt/voicevox_core/libcore.so ./core/lib
#     cp /opt/onnxruntime/lib/* ./core/lib
#     pip3 install -r requirements.txt
#     pip3 install .
# EOF


# Runtime
FROM ${BASE_RUNTIME_IMAGE} AS runtime-env
WORKDIR /opt/voicevox_engine

RUN <<EOF
    apt-get update
    apt-get install -y libsndfile1 gosu
    apt-get clean
    rm -rf /var/lib/apt/lists/*
EOF

COPY --from=download-core-env /opt/voicevox_core /opt/voicevox_core
COPY --from=download-onnxruntime-env /opt/onnxruntime/lib /opt/onnxruntime/lib
COPY --from=download-engine-env /usr/local/lib/python3.8/site-packages /usr/local/lib/python3.8/site-packages
COPY --from=download-engine-env /opt/voicevox_engine /opt/voicevox_engine
COPY --from=download-engine-env /opt/voicevox_dictionary/user.dic /opt/voicevox_engine

COPY ./run_container.py /opt/voicevox_engine/

COPY --chmod=775 ./entrypoint.sh /entrypoint.sh

RUN useradd --create-home user && ldconfig
ENTRYPOINT [ "/entrypoint.sh" ]
ENV PORT=50021
CMD [ "gosu", "user", "python3", "-B", "./run_container.py", "--voicelib_dir", "/opt/voicevox_core", "--runtime_dir", "/opt/onnxruntime/lib", "--host", "0.0.0.0" ]
