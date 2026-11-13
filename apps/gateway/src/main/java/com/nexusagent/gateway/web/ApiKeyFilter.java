package com.nexusagent.gateway.web;

import com.nexusagent.gateway.config.GatewayProperties;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

/**
 * Resolves {@code X-API-Key} to a tenant and rejects unknown keys. Health and
 * actuator routes stay open so probes and the smoke test work without a key.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class ApiKeyFilter implements WebFilter {

    public static final String TENANT_ATTRIBUTE = "nexus.tenant";
    private static final String HEADER = "X-API-Key";

    private final GatewayProperties properties;

    public ApiKeyFilter(GatewayProperties properties) {
        this.properties = properties;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        String path = exchange.getRequest().getPath().value();
        if (isOpen(path)) {
            return chain.filter(exchange);
        }

        Map<String, String> keys = properties.getApiKeys();
        if (keys.isEmpty()) {
            // Auth disabled: local development mode. Still honour an explicit
            // tenant header so isolation can be exercised without API keys.
            String tenant = normalizeTenant(exchange.getRequest().getHeaders().getFirst("X-Tenant-Id"));
            exchange.getAttributes().put(TENANT_ATTRIBUTE, tenant);
            ServerWebExchange mutated = exchange.mutate()
                    .request(builder -> builder.header("X-Tenant-Id", tenant))
                    .build();
            return chain.filter(mutated);
        }

        String key = exchange.getRequest().getHeaders().getFirst(HEADER);
        String tenant = key == null ? null : keys.get(key);
        if (tenant == null) {
            return reject(exchange, HttpStatus.UNAUTHORIZED, "invalid or missing X-API-Key");
        }

        exchange.getAttributes().put(TENANT_ATTRIBUTE, tenant);
        // Downstream sees the resolved tenant, never the raw key.
        ServerWebExchange mutated = exchange.mutate()
                .request(builder -> builder.header("X-Tenant-Id", tenant))
                .build();
        return chain.filter(mutated);
    }

    /** Keeps tenant ids alphanumeric so they can be safely embedded downstream. */
    static String normalizeTenant(String raw) {
        if (raw == null || raw.isBlank()) {
            return "local";
        }
        String cleaned = raw.trim().replaceAll("[^A-Za-z0-9_.-]", "-");
        if (cleaned.length() > 32) {
            cleaned = cleaned.substring(0, 32);
        }
        if (!cleaned.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")) {
            return "local";
        }
        return cleaned;
    }

    static boolean isOpen(String path) {        return path.startsWith("/actuator") || path.equals("/health") || path.equals("/api/v1/health");
    }

    static Mono<Void> reject(ServerWebExchange exchange, HttpStatus status, String message) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        String body = "{\"error\":\"" + message.replace("\"", "'") + "\"}";
        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }
}
