# Lean EMS image (Python 3.12). The React+Vite UI build stage is added in M0b.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29" "pyyaml>=6.0"
COPY ems ./ems
COPY config.yaml ./config.yaml
EXPOSE 8080
CMD ["uvicorn", "ems.main:app", "--host", "0.0.0.0", "--port", "8080"]
