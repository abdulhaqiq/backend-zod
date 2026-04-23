#!/bin/bash
set -e

echo "🚀 Setting up backend auto-deployment on droplet..."

# 1. Create backend directory if it doesn't exist
if [ ! -d "/root/backend" ]; then
    echo "📦 Cloning backend repository..."
    cd /root
    git clone https://github.com/abdulhaqiq/backend-zod.git backend
fi

cd /root/backend

# 2. Checkout main branch and pull latest
echo "🔄 Pulling latest code..."
git fetch origin main
git checkout main
git reset --hard origin/main

# 3. Set up Python virtual environment
if [ ! -d "venv" ]; then
    echo "🐍 Creating Python virtual environment..."
    python3 -m venv venv
fi

# 4. Install dependencies
echo "📚 Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt

# 5. Create systemd service
echo "⚙️  Creating systemd service..."
cat > /etc/systemd/system/backend-api.service << 'EOF'
[Unit]
Description=Ailoo Backend API
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/backend
Environment="PATH=/root/backend/venv/bin"
ExecStart=/root/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 6. Reload systemd and start service
echo "🔄 Reloading systemd..."
systemctl daemon-reload
systemctl enable backend-api
systemctl restart backend-api

# 7. Wait for service to start
sleep 3

# 8. Check status
echo "📊 Checking service status..."
if systemctl is-active --quiet backend-api; then
    echo "✅ Backend API is running!"
    curl -s http://localhost:8000/health | head -20
else
    echo "❌ Backend API failed to start"
    journalctl -u backend-api -n 50 --no-pager
    exit 1
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Add these secrets to GitHub repository settings:"
echo "   - DROPLET_HOST: $(curl -s ifconfig.me)"
echo "   - DROPLET_SSH_KEY: (your SSH private key)"
echo ""
echo "2. The backend will auto-deploy on every git push to main"
