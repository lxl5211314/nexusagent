package com.nexusagent.gateway.config;

import com.nexusagent.gateway.policy.LocalTokenBucketRateLimiter;
import com.nexusagent.gateway.policy.RateLimiter;
import com.nexusagent.gateway.policy.RedisTokenBucketRateLimiter;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Wires the shared limiter and the upstream circuit breaker. */
@Configuration
public class GatewayConfig {

    private static final Logger log = LoggerFactory.getLogger(GatewayConfig.class);

    @Bean
    public RateLimiter rateLimiter(GatewayProperties properties) {
        String url = properties.getRedisUrl();
        if (url == null || url.isBlank()) {
            return new LocalTokenBucketRateLimiter();
        }
        try {
            Config config = new Config();
            config.useSingleServer()
                    .setAddress(url)
                    .setConnectionMinimumIdleSize(1)
                    .setConnectionPoolSize(4);
            RedissonClient client = Redisson.create(config);
            RedisTokenBucketRateLimiter limiter = new RedisTokenBucketRateLimiter(client);
            if (!limiter.ping()) {
                log.warn("redis rate limiter cannot reach {}, falling back to per-instance buckets", url);
                limiter.close();
                return new LocalTokenBucketRateLimiter();
            }
            log.info("rate limiting through redis at {}", url);
            return limiter;
        } catch (RuntimeException ex) {
            // A dead cache must not take the edge down: degrade to per-instance.
            log.warn("redis rate limiter unavailable ({}), using per-instance buckets", ex.getMessage());
            return new LocalTokenBucketRateLimiter();
        }
    }

    @Bean
    public CircuitBreaker upstreamCircuitBreaker(GatewayProperties properties) {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
                .failureRateThreshold(properties.getCircuitFailureRate())
                .slidingWindowSize(properties.getCircuitSlidingWindow())
                .waitDurationInOpenState(properties.getCircuitOpenFor())
                .permittedNumberOfCallsInHalfOpenState(2)
                .build();
        return CircuitBreakerRegistry.of(config).circuitBreaker("upstream");
    }
}
