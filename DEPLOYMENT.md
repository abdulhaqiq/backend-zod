# 🚀 Backend Auto-Deployment

## Server Details
- **Droplet IP**: `134.209.126.56`
- **Production URL**: `http://dev.zod.pro.ailoo.co`
- **Backend Port**: `8000`
- **Service Name**: `backend-api`

## Auto-Deployment Setup ✅

Every time you `git push` to the `main` branch, GitHub Actions will automatically:
1. SSH into your droplet
2. Pull the latest code from GitHub
3. Install any new Python dependencies
4. Restart the backend service
5. Verify the service is running

## Manual Commands (if needed)

### Check backend status
```bash
ssh root@134.209.126.56 "systemctl status backend-api"
```

### View backend logs
```bash
ssh root@134.209.126.56 "journalctl -u backend-api -f"
```

### Restart backend
```bash
ssh root@134.209.126.56 "systemctl restart backend-api"
```

### Check nginx status
```bash
ssh root@134.209.126.56 "systemctl status nginx"
```

## Testing

### Health check
```bash
curl http://dev.zod.pro.ailoo.co/health
# Expected: {"status":"healthy"}
```

### API docs
Visit: http://dev.zod.pro.ailoo.co/docs

## GitHub Secrets (Already Configured)
- `DROPLET_HOST`: 134.209.126.56
- `DROPLET_SSH_KEY`: Your SSH private key

## Directory Structure on Server
```
/root/backend/          # Backend code
/root/backend/venv/     # Python virtual environment
/root/backend/.env      # Environment variables (secrets)
```

## Service Files
- Systemd service: `/etc/systemd/system/backend-api.service`
- Nginx config: `/etc/nginx/sites-available/backend-api`

---

**Setup completed on**: April 23, 2026
