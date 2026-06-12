#!/bin/bash
# deploy.sh
# =========
# Deploy SEO Suggestion Engine backend on EC2.
# Run once after SSHing into your EC2 instance.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh

set -e   # stop on any error

echo "=== SEO Suggestion Engine — EC2 Deployment ==="

# ── 1. Update system ──────────────────────────────────────────
echo "[1/7] Updating system..."
sudo apt-get update -q
sudo apt-get install -y python3-pip python3-venv nginx -q

# ── 2. Create virtual environment ─────────────────────────────
echo "[2/7] Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# ── 3. Install dependencies ───────────────────────────────────
echo "[3/7] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── 4. Set environment variables ──────────────────────────────
echo "[4/7] Setting environment variables..."
if [ ! -f .env ]; then
    cat > .env << 'ENV'
LIGHTHOUSE_API_KEY=your_lighthouse_api_key_here
OPR_API_KEY=your_opr_api_key_here
ENV
    echo "  Created .env file — add your API keys!"
fi

# ── 5. Create __init__.py files ───────────────────────────────
echo "[5/7] Creating package files..."
touch models/__init__.py
touch services/__init__.py
touch api/__init__.py

# ── 6. Check models exist ─────────────────────────────────────
echo "[6/7] Checking models..."
for f in models/xgb_classifier.joblib models/label_encoder.joblib models/clf_feature_cols.joblib; do
    if [ ! -f "$f" ]; then
        echo "  WARNING: $f not found — copy from your notebook!"
    else
        echo "  OK: $f"
    fi
done

# ── 7. Create systemd service ─────────────────────────────────
echo "[7/7] Creating systemd service..."
sudo tee /etc/systemd/system/seo-api.service > /dev/null << SERVICE
[Unit]
Description=SEO Suggestion Engine API
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin
EnvironmentFile=$(pwd)/.env
ExecStart=$(pwd)/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable seo-api
sudo systemctl start seo-api

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Service status:"
sudo systemctl status seo-api --no-pager
echo ""
echo "API running at: http://$(curl -s ifconfig.me):8000"
echo "API docs at   : http://$(curl -s ifconfig.me):8000/docs"
echo ""
echo "NEXT STEPS:"
echo "  1. Add API keys to .env file"
echo "  2. Copy model files to models/ folder"
echo "  3. Run: sudo systemctl restart seo-api"
echo "  4. Test: curl http://localhost:8000/health"
