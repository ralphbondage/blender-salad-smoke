# Salad smoke test: can Blender 5.1.1 render EEVEE (OpenGL/EGL or Vulkan) and Cycles (CUDA/OptiX)
# inside a SaladCloud container? Image is for diagnosis only; contains no secrets and can be public.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ARG BLENDER_VERSION=5.1.1
ARG BLENDER_URL=https://download.blender.org/release/Blender5.1/blender-${BLENDER_VERSION}-linux-x64.tar.xz

ENV DEBIAN_FRONTEND=noninteractive \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,video,display

# libglvnd + EGL/GL/Vulkan loaders (the NVIDIA vendor libs themselves come from the host driver),
# xvfb as a fallback display, mesa-utils-bin for eglinfo, vulkan-tools for vulkaninfo,
# and the X/GL libs the Blender binary links against.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils \
        libegl1 libgl1 libgles2 libglx0 libglvnd0 libvulkan1 \
        mesa-utils-bin vulkan-tools xvfb xauth \
        libxi6 libxrender1 libxkbcommon0 libxkbcommon-x11-0 libsm6 libice6 \
        libxxf86vm1 libxfixes3 libxext6 libx11-6 libxcb1 libdecor-0-0 libwayland-client0 \
        libsndfile1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Tell glvnd / Vulkan loader where the NVIDIA ICDs live (same files nvidia/opengl images ship).
RUN mkdir -p /usr/share/glvnd/egl_vendor.d /etc/vulkan/icd.d \
    && printf '{"file_format_version":"1.0.0","ICD":{"library_path":"libEGL_nvidia.so.0"}}\n' \
        > /usr/share/glvnd/egl_vendor.d/10_nvidia.json \
    && printf '{"file_format_version":"1.0.0","ICD":{"library_path":"libGLX_nvidia.so.0","api_version":"1.3"}}\n' \
        > /etc/vulkan/icd.d/nvidia_icd.json

RUN mkdir -p /opt/blender \
    && curl -fsSL "$BLENDER_URL" | tar -xJ -C /opt/blender --strip-components=1 \
    && ln -s /opt/blender/blender /usr/local/bin/blender

WORKDIR /smoke
COPY smoke_blender.py run_smoke.sh /smoke/
RUN sed -i 's/\r$//' /smoke/run_smoke.sh && chmod +x /smoke/run_smoke.sh

CMD ["/smoke/run_smoke.sh"]
