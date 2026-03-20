from slowapi import Limiter
from slowapi.util import get_remote_address

# Shared limiter instance imported by main.py (to attach to app)
# and by individual routers to add per-route limits.
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
