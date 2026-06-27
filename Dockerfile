# Stage 1 — build the React/Vite SPA (output: ems/web/static/dist).
FROM node:22-slim AS frontend
WORKDIR /app
COPY ems/web/frontend/package.json ems/web/frontend/package-lock.json* ems/web/frontend/
RUN cd ems/web/frontend && npm install
COPY ems/web/frontend ems/web/frontend
RUN cd ems/web/frontend && npm run build

# Stage 2 — lean Python 3.12 runtime serving the API + the built SPA (no runtime CDN).
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "pyyaml>=6.0" "aiosqlite>=0.20"
COPY ems ./ems
COPY config.yaml ./config.yaml
COPY --from=frontend /app/ems/web/static/dist ./ems/web/static/dist
EXPOSE 8080
CMD ["uvicorn", "ems.main:app", "--host", "0.0.0.0", "--port", "8080"]
