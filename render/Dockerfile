# Cycles render worker for SaladCloud: Blender 5.1.1 (CUDA, no OptiX) driven by Kelpie.
# Kelpie pulls a job from its queue, downloads the scene from S3/R2, runs the command, and uploads the frames.
# The image contains no secrets and no scene data: R2 credentials arrive as container-group env vars.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ARG BLENDER_VERSION=5.1.1
ARG BLENDER_URL=https://download.blender.org/release/Blender5.1/blender-${BLENDER_VERSION}-linux-x64.tar.xz
ARG KELPIE_VERSION=0.6.0

ENV DEBIAN_FRONTEND=noninteractive \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Blender links against X/GL client libs even in background mode; nothing here draws to a screen.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils \
        libegl1 libgl1 libgles2 libglx0 libglvnd0 \
        libxi6 libxrender1 libxkbcommon0 libxkbcommon-x11-0 libsm6 libice6 \
        libxxf86vm1 libxfixes3 libxext6 libx11-6 libxcb1 libdecor-0-0 libwayland-client0 \
        libsndfile1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/blender \
    && curl -fsSL "$BLENDER_URL" | tar -xJ -C /opt/blender --strip-components=1 \
    && ln -s /opt/blender/blender /usr/local/bin/blender

ADD https://github.com/SaladTechnologies/kelpie/releases/download/${KELPIE_VERSION}/kelpie /kelpie
RUN chmod +x /kelpie

WORKDIR /work
COPY render_cycles.py /work/render_cycles.py
RUN mkdir -p /work/scene /work/outputs

# Kelpie runs as the main process and executes each job's command from this WORKDIR.
CMD ["/kelpie"]
