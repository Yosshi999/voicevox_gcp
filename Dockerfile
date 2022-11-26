# syntax=docker/dockerfile:1.3-labs

ARG BASE_IMAGE=python:3.8.13-bullseye
ARG BASE_RUNTIME_IMAGE=python:3.8.13-slim-bullseye

# Download ONNX Runtime
FROM ${BASE_IMAGE} AS download-onnxruntime-env
WORKDIR /work

ARG ONNXRUNTIME_URL=https://github.com/microsoft/onnxruntime/releases/download/v1.11.1/onnxruntime-linux-x64-1.11.1.tgz
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

# Download the dictionary
FROM ${BASE_IMAGE} AS download-dict

RUN <<EOF
    set -eux
    apt-get update
    apt-get install -y git cmake wget
    apt-get clean
    rm -rf /var/lib/apt/lists/*
EOF

RUN <<EOF
    set -eux
    wget "https://jaist.dl.sourceforge.net/project/open-jtalk/Dictionary/open_jtalk_dic-1.11/open_jtalk_dic_utf_8-1.11.tar.gz" -O sysdict.tar.gz
    mkdir /opt/dic
    tar xzf sysdict.tar.gz -C /opt/dic
EOF

#RUN pip install git+https://github.com/VOICEVOX/pyopenjtalk@f4ade29ef9a4f43d8605103cb5bacc29e0b2ccae#egg=pyopenjtalk
#RUN <<EOF
#    set -eux
#    mkdir /opt/dic
#    wget -O "additional_openjtalk_dic.zip" https://github.com/takana-v/additional_openjtalk_dic/releases/download/0.0.1/additional_openjtalk_dic.zip
#    unzip additional_openjtalk_dic.zip
#    cat additional_openjtalk_dic/additional_openjtalk_dic.csv >> default.csv
#    python -c "import pyopenjtalk;pyopenjtalk.create_user_dict('default.csv', 'user.dic')"
#    mv user.dic /opt/dic/user.dic
#    rm -rf additional_openjtalk_dic
#    rm additional_openjtalk_dic.zip
#    rm default.csv
#EOF

# Runtime
FROM ${BASE_RUNTIME_IMAGE} AS runtime-env
WORKDIR /opt/voicevox_engine

RUN <<EOF
    apt-get update
    apt-get install -y libsndfile1 gosu
    apt-get clean
    rm -rf /var/lib/apt/lists/*
EOF

RUN pip install https://github.com/VOICEVOX/voicevox_core/releases/download/0.14.0-preview.2/voicevox_core-0.14.0rc2+cpu-cp38-abi3-linux_x86_64.whl
RUN pip install fastapi uvicorn aiofiles soundfile

COPY --from=download-onnxruntime-env /etc/ld.so.conf.d/onnxruntime.conf /etc/ld.so.conf.d/onnxruntime.conf
COPY --from=download-onnxruntime-env /opt/onnxruntime /opt/onnxruntime
COPY --from=download-dict /opt/dic/ /opt/voicevox_engine/dic
#COPY --from=download-dict /opt/dic/user.dic /opt/voicevox_engine/user.dic

COPY ./run_container.py /opt/voicevox_engine/
COPY --chmod=775 ./entrypoint.sh /entrypoint.sh

RUN useradd --create-home user && ldconfig
ENTRYPOINT [ "/entrypoint.sh" ]
ENV PORT=50021
CMD [ "gosu", "user", "python3", "-B", "./run_container.py", "--open_jtalk_dict_dir", "/opt/voicevox_engine/dic/open_jtalk_dic_utf_8-1.11", "--host", "0.0.0.0" ]

