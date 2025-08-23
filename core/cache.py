
import time
_cache = {}

def cache_key(*parts):
    return ":".join([str(p) for p in parts])

def get_cache():
    return MemoryCache()

class MemoryCache:
    def get(self, key):
        now = time.time()
        ent = _cache.get(key)
        if not ent: return None
        value, exp = ent
        if exp is not None and exp < now:
            _cache.pop(key, None); return None
        return value
    def set(self, key, value, ttl=3600):
        exp = time.time() + ttl if ttl else None
        _cache[key] = (value, exp)
