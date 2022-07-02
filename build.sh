#!/bin/bash
export DOCKER_BUILDKIT=1 
docker build -t yosshi999/vvengine-gcp --target runtime-env .

# us-central1-docker.pkg.dev/voicevox-gcr/voicevox/vvengine