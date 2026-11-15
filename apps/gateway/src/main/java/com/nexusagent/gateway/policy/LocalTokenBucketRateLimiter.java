package com.nexusagent.gateway.policy;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-instance token bucket. Correct for a single gateway; with several
 * instances each one would allow its own burst, so configure a Redis URL when
 * the limit has to be global.
 */
public class LocalTokenBucketRateLimiter implements RateLimiter {

    private static final class Bucket {
        private double tokens;
        private long updatedAt;

        Bucket(double tokens, long updatedAt) {
            this.tokens = tokens;
            this.updatedAt = updatedAt;
        }
    }

    private final Map<String, Bucket> buckets = new ConcurrentHashMap<>();

    @Override
    public long tryAcquire(String key, int permitsPerMinute, int burst) {
        int rate = Math.max(1, permitsPerMinute);
        int capacity = Math.max(1, burst);
        long now = System.nanoTime();
        Bucket bucket = buckets.computeIfAbsent(key, ignored -> new Bucket(capacity, now));

        synchronized (bucket) {
            double elapsedSeconds = (now - bucket.updatedAt) / 1_000_000_000.0;
            bucket.tokens = Math.min(capacity, bucket.tokens + elapsedSeconds * rate / 60.0);
            bucket.updatedAt = now;
            if (bucket.tokens >= 1.0) {
                bucket.tokens -= 1.0;
                return -1;
            }
            double missing = 1.0 - bucket.tokens;
            return Math.max(1, (long) Math.ceil(missing * 60.0 / rate));
        }
    }

    public void reset() {
        buckets.clear();
    }
}
