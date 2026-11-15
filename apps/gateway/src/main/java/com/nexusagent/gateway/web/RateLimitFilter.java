package com.nexusagent.gateway.web;

import com.nexusagent.gateway.config.GatewayProperties;
import com.nexusagent.gateway.policy.RateLimiter;
import java.time.Duration;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

/**
 * Rejects a tenant that exhausts its token bucket (HTTP 429). The bucket is keyed
 * by tenant, not by key, so rotating an API key does not reset the budget.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class RateLimitFilter implements WebFilter {

    public static final String RETRY_AFTER_ATTRIBUTE = "nexus.retryAfter";

    private final GatewayProperties properties;
    private final RateLimiter limiter;

    public RateLimitFilter(GatewayProperties properties, RateLimiter limiter) {
        this.properties = properties;
        this.limiter = limiter;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        String path = exchange.getRequest().getPath().value();
        if (path.startsWith("/actuator") || path.equals("/health")) {
            return chain.filter(exchange);
        }

        Object tenant = exchange.getAttributes().get(ApiKeyFilter.TENANT_ATTRIBUTE);
        String bucket = tenant instanceof String value ? value : "anonymous";
        int burst = properties.getRateLimitBurst() > 0
                ? properties.getRateLimitBurst()
                : properties.getRateLimitPerMinute();
        long wait = limiter.tryAcquire(bucket, properties.getRateLimitPerMinute(), burst);
        if (wait >= 0) {
            exchange.getAttributes().put(RETRY_AFTER_ATTRIBUTE, wait);
            exchange.getResponse().getHeaders()
                    .set("Retry-After", Long.toString(Math.max(1, wait)));
            return ApiKeyFilter.reject(exchange, HttpStatus.TOO_MANY_REQUESTS, "rate limit exceeded");
        }
        return chain.filter(exchange);
    }

    /** Kept for the smoke test: a fresh limiter starts with a full bucket. */
    public static Duration window() {
        return Duration.ofMinutes(1);
    }
}
