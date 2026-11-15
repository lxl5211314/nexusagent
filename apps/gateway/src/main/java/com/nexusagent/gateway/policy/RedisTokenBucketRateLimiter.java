package com.nexusagent.gateway.policy;

import java.util.List;
import java.util.concurrent.TimeUnit;
import org.redisson.api.RScript;
import org.redisson.api.RedissonClient;
import org.redisson.client.codec.StringCodec;

/**
 * Shared token bucket. The whole refill-and-take is one Lua script so two
 * gateway instances cannot both read the same token.
 *
 * <pre>
 * KEYS[1] bucket hash: tokens, ts (ms)
 * ARGV[1] capacity, ARGV[2] permits per minute, ARGV[3] now (ms)
 * returns -1 when admitted, otherwise seconds to wait
 * </pre>
 */
public class RedisTokenBucketRateLimiter implements RateLimiter {

    private static final String SCRIPT = """
            local cap = tonumber(ARGV[1])
            local rate = tonumber(ARGV[2])
            local now = tonumber(ARGV[3])
            local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
            local tokens = tonumber(data[1])
            local ts = tonumber(data[2])
            if tokens == nil or ts == nil then
              tokens = cap
              ts = now
            end
            tokens = math.min(cap, tokens + (now - ts) / 60000.0 * rate)
            local allowed = 0
            if tokens >= 1 then
              tokens = tokens - 1
              allowed = 1
            end
            redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
            redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate * 60000) + 1000)
            if allowed == 1 then
              return -1
            end
            return math.ceil((1 - tokens) * 60000 / rate)
            """;

    private final RedissonClient client;

    public RedisTokenBucketRateLimiter(RedissonClient client) {
        this.client = client;
    }

    @Override
    public long tryAcquire(String key, int permitsPerMinute, int burst) {
        int rate = Math.max(1, permitsPerMinute);
        int capacity = Math.max(1, burst);
        long now = System.currentTimeMillis();
        Number answer = client.getScript(StringCodec.INSTANCE).eval(
                RScript.Mode.READ_WRITE,
                SCRIPT,
                RScript.ReturnType.INTEGER,
                List.of("nexus:rate:" + key),
                capacity,
                rate,
                now);
        return answer == null ? -1 : answer.longValue();
    }

    public boolean ping() {
        try {
            client.getKeys().countExists("nexus:rate:health");
            return true;
        } catch (RuntimeException ex) {
            return false;
        }
    }

    public void close() {
        client.shutdown(0, 2, TimeUnit.SECONDS);
    }
}
