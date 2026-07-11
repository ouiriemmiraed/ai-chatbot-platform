import os
from slowapi import Limiter
from slowapi.util import get_remote_address

# Initialize rate limiter — counters are kept in shared Redis so the limits hold
# across all worker processes; falls back to in-memory only if REDIS_URL is unset.
_storage_uri = os.getenv("REDIS_URL")
if _storage_uri:
    limiter = Limiter(key_func=get_remote_address, storage_uri=_storage_uri)
else:
    limiter = Limiter(key_func=get_remote_address)
