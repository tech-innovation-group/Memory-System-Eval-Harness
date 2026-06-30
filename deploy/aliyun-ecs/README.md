## Aliyun ECS Deployment

This project can run on an Alibaba Cloud ECS instance.

## One-click options

### Local machine

From the repo root:

```bash
chmod +x one_click_start.sh
./one_click_start.sh
```

Behavior:

- loads `.env.local` automatically when present
- runs `./preflight.sh`
- starts the web service with `./start.sh`

Optional:

```bash
RUN_PREFLIGHT=0 ./one_click_start.sh
LOCOMO_ENV_FILE=/absolute/path/to/other.env ./one_click_start.sh
```

### Alibaba Cloud ECS

After the repo is already on the server:

```bash
cd /opt/locomo/locomo-eval-web
sudo bash deploy/aliyun-ecs/install.sh
```

Useful follow-up commands:

```bash
bash deploy/aliyun-ecs/check.sh
bash deploy/aliyun-ecs/update.sh
```

Recommended baseline:

- OS: Ubuntu 22.04 LTS
- CPU / RAM:
  - UI-only and light smoke runs: 2 vCPU / 4 GB
  - Real EchoMemory QA runs: 4 vCPU / 8 GB or higher
- Disk: 80 GB SSD minimum
- Public access: one domain name pointed at the ECS public IP

This repo is a plain Python HTTP server:

- entrypoint: `server.py`
- launcher: `start.sh`
- bind host/port: `LOCOMO_EVAL_HOST` / `LOCOMO_EVAL_PORT`

Recommended production shape:

1. run the app behind `systemd`
2. reverse proxy with `nginx`
3. terminate HTTPS at `nginx`
4. keep the app bound to `127.0.0.1:19181`

### 1. Prepare the server

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

If you will run EchoMemory on the same machine, also prepare the EchoMemory repo and its `.venv`.

### 2. Clone the repos

```bash
sudo mkdir -p /opt/locomo
sudo chown -R $USER:$USER /opt/locomo

git clone <your-harness-repo-url> /opt/locomo/locomo-eval-web
git clone <your-echomemory-repo-url> /opt/locomo/echo_memory
```

### 3. Create Python environments

Harness:

```bash
cd /opt/locomo/locomo-eval-web
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
```

EchoMemory:

```bash
cd /opt/locomo/echo_memory
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Notes:

- The harness itself uses the standard library for the web server.
- EchoMemory runtime dependencies come from the EchoMemory repo you install with `pip install -e .`.

### 4. Configure environment variables

Copy the sample:

```bash
cd /opt/locomo/locomo-eval-web
cp deploy/aliyun-ecs/locomo-eval.env.sample .env.production
```

Then edit `.env.production`.

Minimum fields you must set:

- `LOCOMO_EVAL_HOST=127.0.0.1`
- `LOCOMO_EVAL_PORT=19181`
- `ECHOMEM_ROOT=/opt/locomo/echo_memory`
- `ECHOMEM_PYTHON=/opt/locomo/echo_memory/.venv/bin/python`
- `ECHOMEM_WORKSPACE=/data/echomem_workspace`
- `JUDGE_BASE_URL`
- `JUDGE_MODEL`
- `JUDGE_TOKEN`
- provider API keys for EchoMemory runtime

### 5. Run preflight locally on the server

```bash
cd /opt/locomo/locomo-eval-web
set -a
source ./.env.production
set +a
./preflight.sh
```

### 6. Install the systemd service

Copy the template and adjust paths and user:

```bash
sudo cp deploy/aliyun-ecs/locomo-eval.service /etc/systemd/system/locomo-eval.service
sudo systemctl daemon-reload
sudo systemctl enable locomo-eval
sudo systemctl start locomo-eval
sudo systemctl status locomo-eval --no-pager
```

Health check:

```bash
curl -s http://127.0.0.1:19181/health
```

### 7. Configure nginx

Copy the template:

```bash
sudo cp deploy/aliyun-ecs/nginx.locomo-eval.conf /etc/nginx/sites-available/locomo-eval
sudo ln -sf /etc/nginx/sites-available/locomo-eval /etc/nginx/sites-enabled/locomo-eval
sudo nginx -t
sudo systemctl reload nginx
```

### 8. Enable HTTPS

Point your domain to the ECS public IP first, then run:

```bash
sudo certbot --nginx -d your-domain.example.com
```

### 9. Alibaba Cloud console settings

Open these ECS security-group ports:

- `22/tcp` for SSH
- `80/tcp` for HTTP
- `443/tcp` for HTTPS

Do not expose `19181` publicly when nginx is in front of it.

### 10. Operations

Useful commands:

```bash
sudo systemctl restart locomo-eval
sudo systemctl status locomo-eval --no-pager
sudo journalctl -u locomo-eval -n 200 --no-pager
curl -s http://127.0.0.1:19181/api/readiness | python3 -m json.tool | head -120
```

### Deployment choice

There are two realistic deployment modes:

1. `UI + harness only` on ECS:
   - easier
   - enough if the machine only orchestrates existing model endpoints and local EchoMemory code

2. `UI + harness + EchoMemory runtime` on the same ECS:
   - what this template assumes
   - requires enough CPU, RAM, and disk for your real runs

Main install parameters:

- `APP_DIR`
- `SERVICE_NAME`
- `SERVICE_USER`
- `ENV_FILE`
- `APP_PORT`
- `DOMAIN`
- `INSTALL_NGINX=1|0`
- `ENABLE_HTTPS=1|0`

Example:

```bash
cd /opt/locomo/locomo-eval-web
sudo DOMAIN=eval.example.com SERVICE_USER=ubuntu ENABLE_HTTPS=1 bash deploy/aliyun-ecs/install.sh
```
