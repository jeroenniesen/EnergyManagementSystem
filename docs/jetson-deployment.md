# Jetson deployment guide

> Companion to `../SPEC.md §11` (Raspberry Pi variant) and `docs/ml-layer.md`. This doc covers running the EMS on an **NVIDIA Jetson** — the platform that enables the optional GPU-accelerated ML layer (`docs/ml-layer.md`). The key topology difference from §11: **the Jetson runs the EMS + the ML sidecar only; Home Assistant and the MQTT broker live on a separate host on the LAN** (e.g. a dedicated Pi running HA Container). The same codebase runs on a plain Pi (CPU-only, no ML) or a Jetson (ML lit up via `ml.enabled`); this doc is the Jetson variant.

---

## 1. Hardware and OS

| Item | Target | Notes |
|---|---|---|
| **JetPack** | 6.x (L4T r36, Ubuntu 22.04 arm64) | Exact JetPack/L4T version is a **CONFIRM@deploy** item — see `../SPEC.md §17`. |
| **RAM / VRAM** | ~8 GB shared (e.g. Orin Nano) | HA is **not** on this host, so all RAM is for EMS + ML. VRAM budget: **CONFIRM@deploy**. |
| **Storage** | NVMe SSD (recommended) | Models + SQLite writes; avoid SD. |
| **Architecture** | arm64 — same as Pi 5 | But the CUDA userspace and driver are **pinned to the JetPack version**; the Pi image is CPU-only arm64 and must never carry GPU deps. |

> **CONFIRM@deploy:** run `jetson_release` and `nvidia-smi` on first boot to record the exact JetPack/L4T/CUDA version and available VRAM before sizing the ML models.

---

## 2. Container runtime (NVIDIA)

Install `nvidia-container-toolkit` from the JetPack apt feed (not upstream Docker Hub):

```bash
sudo apt-get install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify:

```bash
docker run --rm --runtime=nvidia nvcr.io/nvidia/l4t-base:r36.x nvidia-smi
```

**Giving a container the GPU:**
- Compose (v3.x `deploy` block, recommended): `deploy.resources.reservations.devices` with `driver: nvidia` (see §4 compose sketch).
- Compose (legacy): `runtime: nvidia` on the service.
- One-off: `docker run --gpus all …`.

Only the `ml` sidecar container needs the GPU. The `ems` container is CPU-only and runs identically on the Pi.

---

## 3. Image strategy — two images, isolated deps

| Image | Base | GPU deps | Runs on |
|---|---|---|---|
| **`ems`** (lean) | `python:3.12-slim` (multi-stage: Node → Python) | None | Pi **and** Jetson |
| **`ml`** (sidecar) | `nvcr.io/nvidia/l4t-*` (JetPack CUDA base) | `onnxruntime-gpu`, `torch`, `tensorrt`, `llama.cpp` / `ollama` | Jetson only |

**Multi-stage EMS build** (same Dockerfile as the Pi build — no changes):

```dockerfile
# Stage 1 — build the React+Vite UI
FROM node:20-slim AS ui-build
WORKDIR /ui
COPY ems/web/ui/package*.json ./
RUN npm ci
COPY ems/web/ui/ ./
RUN npm run build          # emits dist/

# Stage 2 — lean Python image
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ems/ ./ems/
COPY --from=ui-build /ui/dist ./ems/web/static/dist
CMD ["uvicorn", "ems.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

`requirements.txt` contains **no GPU packages**. The `ml` image carries all GPU deps in a separate `requirements-ml.txt`.

**EMS ↔ ML communication:** the `ml` sidecar exposes a lightweight HTTP API on `localhost:9090` (or a compose-internal bridge network). The EMS calls it for inference when `ml.enabled: true` and the `ml` container is healthy. If the sidecar is absent or returns a non-200, the EMS falls back to the rule-based/CPU path automatically and logs the fallback — see §5 (capability detection).

---

## 4. docker-compose.yml (Jetson sketch)

```yaml
# docker-compose.yml — Jetson variant
# HA and MQTT broker live on a REMOTE host; point at their LAN IP.
# Compare: ../SPEC.md §11 (single-host Pi compose, network_mode: host).
# No network_mode: host needed here — HA is remote, not on this machine.

services:

  ems:
    build:
      context: .
      dockerfile: Dockerfile          # multi-stage, CPU-only (same as Pi)
    ports:
      - "8080:8080"                   # EMS web UI — reachable on the LAN
    environment:
      HA_URL: http://<remote-ha-ip>:8123
      MQTT_HOST: <remote-ha-ip>       # broker on the HA host
      MQTT_PORT: "1883"
      ML_SIDECAR_URL: http://ml:9090  # internal; absent on Pi
    volumes:
      - ./ems/config.yaml:/app/config.yaml:ro
      - ems_data:/data                # SQLite + backups + runtime settings
    mem_limit: 512m
    cpus: 1.0
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health/ready').status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
    stop_grace_period: 30s
    depends_on:
      ml:
        condition: service_started    # ordering only — EMS NEVER waits for ML health (ML is optional)
    restart: unless-stopped

  ml:
    build:
      context: .
      dockerfile: Dockerfile.ml       # FROM nvcr.io/nvidia/l4t-* + GPU deps
    expose:
      - "9090"                        # EMS-internal only; not published to LAN
    volumes:
      - ml_models:/data/models        # model artifacts (ONNX, GGUF, TorchScript)
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    mem_limit: 6g                     # remainder after EMS; VRAM is shared — CONFIRM@deploy
    healthcheck:
      test: ["CMD", "curl", "-fs", "http://localhost:9090/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s               # model load takes time
    restart: unless-stopped

volumes:
  ems_data:
  ml_models:
```

> **The `ml` dependency is intentionally soft** (`service_started`, not `service_healthy`): it only orders container startup — the EMS **never blocks on ML being ready** and starts/runs fully whether or not ML ever comes up. Runtime capability detection (§5) loads the ML adapters only when present and **degrades to the rule-based/baseline path** otherwise (`ml.enabled` / `ml.require_accelerator`), matching the "ML helps, never rules" intent. Drop the `depends_on` entirely if you prefer no coupling at all.

> **No `network_mode: host`** — unlike the Pi single-host setup, HA is remote; there is no local mDNS/USB/BT need on the Jetson host. The `ems` container is reachable on the LAN via the host's IP on port 8080.

---

## 5. Capability detection and the `ml.enabled` gate

At boot the EMS runs GPU capability detection (mirroring the battery `CapabilityReport` pattern in `../SPEC.md §6.5`):

1. Probe the `ml` sidecar at `ML_SIDECAR_URL/health` — record GPU visible (`cuda_available`), loaded models, and their VRAM footprint.
2. If `ml.enabled: false` (config) or the sidecar is unreachable or no accelerator is available → load **baseline/rule-based adapters only** (the CPU path). Raise an alert if `ml.require_accelerator: true` and none is found.
3. The planner depends on **ports** (forecast provider, optimizer, explainer interfaces) — not on concrete ML classes. The ML adapters and the statistical/rule-based adapters satisfy the same interfaces (`docs/ml-layer.md`). Nothing downstream knows or cares which is active.

The identical `ems` image is therefore the Pi build too — `ml.enabled: false` (default) with no sidecar in the Pi compose simply loads the CPU path. Portability requires no code changes.

```yaml
# config.yaml additions for Jetson
ml:
  enabled: true                 # false on Pi (default)
  require_accelerator: true     # ML models load only when a supported accelerator (here, CUDA) is detected; absent → graceful fallback to baseline (never refuses to start)
  sidecar_url: http://ml:9090
  inference_timeout_seconds: 5  # fall back to rule-based if the sidecar is slow
```

---

## 6. Resource budgeting

| Component | RAM budget | VRAM notes |
|---|---|---|
| `ems` container | 512 MB | CPU-only; no GPU allocation |
| `ml` sidecar | ~6 GB | LLM explainer + optimizer + forecaster — **CONFIRM@deploy** that models fit |
| OS + Docker overhead | ~1–1.5 GB | Shared RAM is tight on an 8 GB Orin Nano |

HA is **not** on this host, so it does not compete for RAM or VRAM. Total VRAM consumed by loaded models is a **CONFIRM@deploy** item — run `tegrastats` or `nvidia-smi` with models loaded before enabling live control.

---

## 7. Operational parity with `../SPEC.md §11`

All Pi hardening applies unchanged on the Jetson:

| Concern | Behaviour |
|---|---|
| **Graceful shutdown** | SIGTERM → finish current DB write, stop loop, **no new battery commands**, battery left in its current safe mode (`stop_grace_period: 30s`). On shutdown the battery reverts to its vendor safe mode (self-consumption / `AUTO`) via the restore-original logic (`../SPEC.md §6.5`). |
| **Backups** | Scheduled copy of `/data/ems.sqlite` and `config.yaml` to `history.backup_dir`; additionally back up `/data/models` (model artifacts) if models are fine-tuned locally. Token *locations* noted, never values. Restore procedure: stop → copy files → restart → verify `/health/ready`. |
| **NTP** | Clock sync is critical for price/charge windows. `health.ntp_check` alerts on drift; configure `systemd-timesyncd` or `chrony` on the Jetson OS. |
| **Log rotation** | Rotating file logs (size/age capped). Tokens redacted from logs and debug dumps. |
| **DB maintenance** | `retention_days` purge + `vacuum_on_start` (`history.db_path: /data/ems.sqlite`). |
| **Healthchecks** | `GET /health/live` (process up), `GET /health/ready` (config loaded, remote HA reachable or explicit-degraded, DB writable). Compose `healthcheck` polls `/health/ready`. |
| **Resource limits** | `mem_limit` + `cpus` on `ems`; `mem_limit` on `ml` (§6). |
| **Restart policy** | `restart: unless-stopped` on both services. |

---

## 8. Thermal and power notes

Jetson power modes (set with `nvpmodel`) and thermal throttling affect ML inference latency. **ML inference is off the control critical path** — the EMS's 5-minute control cycle can tolerate a several-second sidecar timeout; it falls back to the rule-based path if the sidecar is slow (see `ml.inference_timeout_seconds`, §5). Thermal limits therefore never risk control safety. Confirm the target `nvpmodel` profile and cooling solution on the device before deploying with `ml.require_accelerator: true`.

---

## 9. Validation checklist (Jetson-specific)

Run these in addition to the main validation checklist in `../SPEC.md` (top of spec):

- [ ] **JetPack version confirmed** — `jetson_release` output recorded; matches the `l4t-*` base tag in `Dockerfile.ml`.
- [ ] **GPU visible inside the `ml` container** — `docker compose exec ml nvidia-smi` returns the device.
- [ ] **Models fit in VRAM** — `tegrastats` or `nvidia-smi` with all models loaded; no OOM.
- [ ] **Remote HA reachable** — `curl http://<remote-ha-ip>:8123/api/` returns 200 from the Jetson host; HA URL set in compose env.
- [ ] **Remote MQTT broker reachable** — `mosquitto_pub -h <remote-ha-ip> -t test -m ok` succeeds from the Jetson.
- [ ] **EMS image runs CPU-only** — start the `ems` service alone (no `ml` container, `ml.enabled: false`) and confirm `/health/ready` returns 200; proves Pi portability.
- [ ] **ML sidecar healthcheck passes** — `docker compose ps ml` shows `healthy` before enabling live control.
- [ ] **`nvpmodel` profile set** — confirm the Jetson power mode is appropriate for sustained inference; check thermal margins under load.
