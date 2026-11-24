#!/usr/bin/env bash
# Salad smoke test entrypoint. Runs a matrix of Blender render attempts, prints a summary
# between SMOKE_SUMMARY_BEGIN / SMOKE_SUMMARY_END, optionally PUTs it to $RESULT_URL, then
# sleeps $HOLD_SECONDS (default 600) so the logs can be read in the Salad portal before exit.
# Nothing here is destructive; it only writes under /tmp.
set -u
HOLD_SECONDS="${HOLD_SECONDS:-600}"
RESULTS=/tmp/smoke_results.jsonl
: > "$RESULTS"

section() { printf '\n===== %s =====\n' "$1"; }

section "environment"
date -u
uname -a
echo "NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-}"
nproc; free -m | head -2; df -h /tmp | tail -1

section "nvidia-smi"
nvidia-smi 2>&1 | head -20 || echo "nvidia-smi missing"

section "driver libs visible"
ldconfig -p | grep -Ei 'libEGL_nvidia|libGLX_nvidia|libnvidia-glcore|libnvidia-vulkan|libnvoptix|libcuda\.so|libnvidia-rtcore' || echo "no NVIDIA libs listed by ldconfig"
ls -la /dev/dxg /dev/nvidia* /usr/lib/wsl/lib 2>&1 | head -30

section "eglinfo (surfaceless/device platform)"
timeout 60 eglinfo -B 2>&1 | head -40 || echo "eglinfo failed"

section "vulkaninfo"
timeout 60 vulkaninfo --summary 2>&1 | head -40 || echo "vulkaninfo failed"

run_case() {  # name, command...
  local name="$1"; shift
  section "case: $name"
  local log="/tmp/case_${name}.log"
  timeout 600 "$@" >"$log" 2>&1
  local rc=$?
  tail -n 25 "$log"
  local line
  line=$(grep -h '^SMOKE_JSON=' "$log" | tail -1 | sed 's/^SMOKE_JSON=//')
  [ -z "$line" ] && line="{\"ok\":false,\"error\":\"no SMOKE_JSON (exit $rc, likely crash)\"}"
  echo "{\"case\":\"$name\",\"exit\":$rc,\"result\":$line}" >> "$RESULTS"
}

section "which GL renderer would Blender get? (llvmpipe = CPU software, NOT a real GPU)"
EGL_R=$(timeout 60 eglinfo -B 2>&1 | grep -iE 'renderer' | head -3 | tr '\n' ';')
GLX_R=$(timeout 60 xvfb-run -a -s "-screen 0 1280x720x24 +extension GLX" glxinfo -B 2>&1 | grep -iE 'renderer' | head -2 | tr '\n' ';')
echo "EGL: $EGL_R"; echo "GLX(xvfb): $GLX_R"
echo "{\"case\":\"gl_renderer_probe\",\"egl\":\"${EGL_R//\"/}\",\"glx_xvfb\":\"${GLX_R//\"/}\",\"software_only\":$(echo "$EGL_R $GLX_R" | grep -qi llvmpipe && echo true || echo false)}" >> "$RESULTS"

B="blender -b --factory-startup --python /smoke/smoke_blender.py"
run_case eevee_opengl_egl     $B -- eevee /tmp/eevee_gl.png
run_case eevee_opengl_xvfb    xvfb-run -a -s "-screen 0 1280x720x24 +extension GLX" $B -- eevee /tmp/eevee_xvfb.png
run_case eevee_vulkan         blender -b --factory-startup --gpu-backend vulkan --python /smoke/smoke_blender.py -- eevee /tmp/eevee_vk.png
run_case cycles_gpu           $B -- cycles /tmp/cycles.png

section "SUMMARY"
echo SMOKE_SUMMARY_BEGIN
cat "$RESULTS"
echo SMOKE_SUMMARY_END

if [ -n "${RESULT_URL:-}" ]; then
  section "uploading summary to RESULT_URL"
  curl -fsS -X PUT --data-binary @"$RESULTS" "$RESULT_URL" && echo "uploaded" || echo "upload failed"
fi

echo "Holding ${HOLD_SECONDS}s so logs can be read; then exiting."
sleep "$HOLD_SECONDS"
exit 0
