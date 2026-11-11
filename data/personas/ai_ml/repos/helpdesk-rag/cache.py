import json
import redis

r = redis.Redis.from_url("redis://localhost:6379/1")


def cached(key: str, ttl: int, fn):
    hit = r.get(key)
    if hit:
        return json.loads(hit)
    value = fn()
    r.setex(key, ttl, json.dumps(value))
    return value
