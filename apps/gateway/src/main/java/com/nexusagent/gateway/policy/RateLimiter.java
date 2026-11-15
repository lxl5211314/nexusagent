package com.nexusagent.gateway.policy;

/**
 * Token-bucket admission control. A {@code -1} answer means "allowed"; any other
 * value is the number of seconds the caller should wait before retrying.
 */
public interface RateLimiter {

    long tryAcquire(String key, int permitsPerMinute, int burst);

    default long tryAcquire(String key, int permitsPerMinute) {
        return tryAcquire(key, permitsPerMinute, permitsPerMinute);
    }
}
