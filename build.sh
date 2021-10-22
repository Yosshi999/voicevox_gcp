#!/bin/bash
export DOCKER_BUILDKIT=1 
docker build -t yosshi999/vvengine-gcp --target runtime-env .
