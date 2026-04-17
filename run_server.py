import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Use the venv's Python if it exists, otherwise fall back to the current interpreter
_venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python")
_python = _venv_python if os.path.exists(_venv_python) else sys.executable

# Kill anything already on port 8000 so we never get "Address already in use"
result = subprocess.run(["lsof", "-ti", ":8000"], capture_output=True, text=True)
pids = result.stdout.strip().split()
if pids:
    subprocess.run(["kill", "-9"] + pids, capture_output=True)
    print(f"Killed existing process(es) on port 8000: {', '.join(pids)}")

subprocess.run(
    [_python, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
    check=True,
)
