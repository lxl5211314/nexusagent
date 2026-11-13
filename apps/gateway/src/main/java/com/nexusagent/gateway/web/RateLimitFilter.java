package com.nexusagent.gateway.web;

import com.nexusagent.gateway.config.GatewayProperties;
import com.nexusagent.gateway.policy.FixedWindowRateLimiter;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

/** Rejects a tenant that exceeds the per-minute budget (HTTP 429). */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class RateLimitFilter implements WebFilter {

    private final GatewayProperties properties;
    private final FixedWindowRateLimiter limiter;

    public RateLimitFilter(GatewayProperties properties, FixedWindowRateLimiter limiter) {
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
        if (!limiter.allow(bucket, properties.getRateLimitPerMinute())) {
            return ApiKeyFilter.reject(exchange, HttpStatus.TOO_MANY_REQUESTS, "rate limit exceeded");
        }
        return chain.filter(exchange);
    }
}
