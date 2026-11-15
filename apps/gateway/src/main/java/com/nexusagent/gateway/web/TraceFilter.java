package com.nexusagent.gateway.web;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.util.UUID;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

/**
 * One trace id per request: read {@code X-Request-Id} or mint a short one, put it
 * in the MDC (logback pattern prints it), echo it back, and record latency per
 * route so a slow upstream is visible in Prometheus without a tracer agent.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 20)
public class TraceFilter implements WebFilter {

    public static final String TRACE_ATTRIBUTE = "nexus.traceId";
    public static final String TRACE_HEADER = "X-Trace-Id";

    private final MeterRegistry meters;

    public TraceFilter(MeterRegistry meters) {
        this.meters = meters;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        String incoming = exchange.getRequest().getHeaders().getFirst(TRACE_HEADER);
        String traced = TraceBridge.currentTraceId();
        String traceId = traced != null
                ? traced
                : incoming == null || incoming.isBlank()
                        ? UUID.randomUUID().toString().replace("-", "").substring(0, 16)
                        : incoming.trim().substring(0, Math.min(32, incoming.trim().length()));

        exchange.getAttributes().put(TRACE_ATTRIBUTE, traceId);
        exchange.getResponse().getHeaders().set(TRACE_HEADER, traceId);
        MDC.put("traceId", traceId);

        String route = route(exchange.getRequest().getPath().value());
        Timer.Sample sample = Timer.start(meters);
        try {
            return chain.filter(exchange).doFinally(signal -> {
                sample.stop(Timer.builder("gateway.request")
                        .tag("route", route)
                        .tag("signal", signal.toString())
                        .publishPercentileHistogram()
                        .register(meters));
                MDC.remove("traceId");
            });
        } catch (RuntimeException ex) {
            MDC.remove("traceId");
            throw ex;
        }
    }

    /** Collapses ids so metric cardinality stays bounded. */
    static String route(String path) {
        if (path.startsWith("/api/v1/runs/")) {
            return "/api/v1/runs/{id}/stream";
        }
        if (path.startsWith("/api/v1/kb/") && path.endsWith("/documents")) {
            return "/api/v1/kb/{id}/documents";
        }
        return path;
    }

    static Counter rejected(MeterRegistry meters, String reason) {
        return Counter.builder("gateway.rejected").tag("reason", reason).register(meters);
    }
}
