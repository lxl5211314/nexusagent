package com.nexusagent.gateway.policy;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

import org.springframework.stereotype.Component;

/**
 * Fixed-window counter per tenant. Good enough for edge protection: no shared
 * state, no locks on the hot path, and a bounded map (evicts on window rollover).
 */
@Component
public class FixedWindowRateLimiter {

    private static final long WINDOW_MS = 60_000L;

    private record Window(long startedAt, AtomicLong count) {
    }

    private final Map<String, Window> windows = new ConcurrentHashMap<>();

    public boolean allow(String key, int limit) {
        long now = System.currentTimeMillis();
        Window window = windows.compute(key, (ignored, current) -> {
            if (current == null || now - current.startedAt() >= WINDOW_MS) {
                return new Window(now, new AtomicLong(0));
            }
            return current;
        });
        return window.count().incrementAndGet() <= limit;
    }

    public void reset() {
        windows.clear();
    }
}
