"""Drive the Salad smoke test through the SaladCloud public API (stdlib only).

  python salad_smoke.py gpu-classes                 # list GPU classes (read-only)
  python salad_smoke.py create --image IMG [--gpu 4090] [--priority batch] [--go]
  python salad_smoke.py start|status|logs|delete [--go for delete]

Env: SALAD_API_KEY, SALAD_ORG, SALAD_PROJECT. `create` and `delete` only PRINT what they would
send unless you pass --go. `start` begins billing (per second while an instance runs); `delete`
removes the group. The group is always named GROUP_NAME below.
Endpoint and field names follow docs.salad.com (create-container-group); the request body was
summarised from the docs, not verified against a live call, so a 400 reply may need a field fix.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://api.salad.com/api/public"
GROUP_NAME = "blender-smoke-test"


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env var {name}")
    return v


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Salad-Api-Key": env("SALAD_API_KEY"), "accept": "application/json",
                 "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def grp_path():
    return f"/organizations/{env('SALAD_ORG')}/projects/{env('SALAD_PROJECT')}/containers"


def gpu_classes():
    st, data = call("GET", f"/organizations/{env('SALAD_ORG')}/gpu-classes")
    if st != 200:
        sys.exit(f"gpu-classes failed: {st} {data}")
    return data["items"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gpu-classes", "create", "start", "status", "logs", "delete"])
    ap.add_argument("--image")
    ap.add_argument("--gpu", default="4090", help="substring of GPU class name, e.g. 4090 or 5090")
    ap.add_argument("--priority", default="batch", choices=["batch", "low", "medium", "high"])
    ap.add_argument("--result-url", help="optional pre-signed PUT URL for the results summary")
    ap.add_argument("--go", action="store_true", help="actually send create/delete")
    a = ap.parse_args()

    if a.cmd == "gpu-classes":
        for g in gpu_classes():
            print(g["id"], g["name"], "prices:", json.dumps(g.get("prices")))
        return

    if a.cmd == "create":
        if not a.image:
            sys.exit("--image required")
        match = [g for g in gpu_classes() if a.gpu.lower() in g["name"].lower()]
        if not match:
            sys.exit(f"no GPU class name contains {a.gpu!r}; run gpu-classes")
        env_vars = {"HOLD_SECONDS": "900"}
        if a.result_url:
            env_vars["RESULT_URL"] = a.result_url
        body = {
            "name": GROUP_NAME,
            "display_name": "Blender smoke test",
            "replicas": 1,
            "restart_policy": "never",
            "autostart_policy": False,
            "container": {
                "image": a.image,
                "resources": {"cpu": 4, "memory": 16384,
                              "gpu_classes": [match[0]["id"]],
                              "storage_amount": 30 * 1024 ** 3},
                "priority": a.priority,
                "environment_variables": env_vars,
            },
            "networking": {"protocol": "http", "auth": True, "port": 8080},
        }
        print("GPU class:", match[0]["name"], match[0]["id"])
        print(json.dumps(body, indent=2))
        if not a.go:
            print("\n(dry run: nothing sent; add --go to create the group, which does not start it)")
            return
        st, data = call("POST", grp_path(), body)
        print(st, json.dumps(data, indent=2) if isinstance(data, dict) else data)
    elif a.cmd == "start":
        print(*call("POST", f"{grp_path()}/{GROUP_NAME}/start"))
    elif a.cmd == "status":
        st, data = call("GET", f"{grp_path()}/{GROUP_NAME}")
        print(st, json.dumps(data, indent=2) if isinstance(data, dict) else data)
    elif a.cmd == "logs":
        # system events only (allocation, download, start, stop). Container stdout is in the Salad
        # portal's Logs tab, or via an external logging provider / the Query Log Entries API.
        st, data = call("GET", f"{grp_path()}/{GROUP_NAME}/system-logs")
        print(st, json.dumps(data, indent=2) if isinstance(data, dict) else data)
    elif a.cmd == "delete":
        if not a.go:
            print(f"dry run: would DELETE {grp_path()}/{GROUP_NAME}; add --go")
            return
        print(*call("DELETE", f"{grp_path()}/{GROUP_NAME}"))


if __name__ == "__main__":
    main()
