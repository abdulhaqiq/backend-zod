import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Kill anything already on port 8000 so we never get "Address already in use"
result = subprocess.run(["lsof", "-ti", ":8000"], capture_output=True, text=True)
pids = result.stdout.strip().split()
if pids:
    subprocess.run(["kill", "-9"] + pids, capture_output=True)
    print(f"Killed existing process(es) on port 8000: {', '.join(pids)}")

subprocess.run(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
    check=True,
)
