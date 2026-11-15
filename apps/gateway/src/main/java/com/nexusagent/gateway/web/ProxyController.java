package com.nexusagent.gateway.web;

import com.fasterxml.jackson.databind.JsonNode;
import com.nexusagent.gateway.config.GatewayProperties;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.MultipartBodyBuilder;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.reactive.function.BodyInserters;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/**
 * Thin proxy to the Python engine. The gateway owns auth/tenant/limits; it never
 * parses the SSE frames, so the agent's event schema can evolve independently.
 *
 * <p>Tenant propagation: the filters resolve {@code X-Tenant-Id} into an
 * exchange attribute; every upstream call below forwards it explicitly. Without
 * that the WebClient request would silently drop the header and the Python side
 * would see the default tenant — isolation would fail in the worst way (silently
 * merging tenants).
 */
@RestController
@RequestMapping("/api/v1")
public class ProxyController {

    private static final ParameterizedTypeReference<ServerSentEvent<String>> SSE_TYPE =
            new ParameterizedTypeReference<>() {
            };

    private final WebClient client;
    private final Duration timeout;
    private final CircuitBreaker breaker;

    public ProxyController(GatewayProperties properties, CircuitBreaker upstreamCircuitBreaker) {
        this.client = WebClient.builder().baseUrl(properties.getPythonBaseUrl()).build();
        this.timeout = properties.getUpstreamTimeout();
        this.breaker = upstreamCircuitBreaker;
    }

    private static String tenant(ServerWebExchange exchange) {
        return attribute(exchange, ApiKeyFilter.TENANT_ATTRIBUTE, "local");
    }

    private static String role(ServerWebExchange exchange) {
        return attribute(exchange, ApiKeyFilter.ROLE_ATTRIBUTE, "admin");
    }

    private static String user(ServerWebExchange exchange) {
        return attribute(exchange, ApiKeyFilter.USER_ATTRIBUTE, "anonymous");
    }

    private static String trace(ServerWebExchange exchange) {
        return attribute(exchange, TraceFilter.TRACE_ATTRIBUTE, "");
    }

    private static String attribute(ServerWebExchange exchange, String name, String fallback) {
        Object value = exchange.getAttribute(name);
        return value == null ? fallback : value.toString();
    }

    /** Every upstream call carries the resolved identity and the trace id. */
    private WebClient.RequestHeadersSpec<?> identity(
            WebClient.RequestHeadersSpec<?> spec, ServerWebExchange exchange) {
        WebClient.RequestHeadersSpec<?> withTenant = spec
                .header("X-Tenant-Id", tenant(exchange))
                .header("X-User-Role", role(exchange))
                .header("X-User-Id", user(exchange));
        String traceId = trace(exchange);
        return traceId.isEmpty() ? withTenant : withTenant.header("X-Trace-Id", traceId);
    }

    /**
     * Counts one breaker call around the upstream exchange. An open circuit
     * short-circuits to 503 instead of piling more work onto a sick engine.
     */
    private <T> Mono<T> guarded(Mono<T> call) {
        return Mono.defer(() -> {
            if (!breaker.tryAcquirePermission()) {
                return Mono.error(new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE, "upstream circuit is open"));
            }
            long started = System.nanoTime();
            return call
                    .doOnSuccess(ignored -> breaker.onSuccess(System.nanoTime() - started, TimeUnit.NANOSECONDS))
                    .doOnError(error -> breaker.onError(
                            System.nanoTime() - started, TimeUnit.NANOSECONDS, error));
        });
    }

    private <T> Flux<T> guardedStream(Flux<T> call) {
        return Flux.defer(() -> {
            if (!breaker.tryAcquirePermission()) {
                return Flux.error(new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE, "upstream circuit is open"));
            }
            long started = System.nanoTime();
            return call
                    .doOnComplete(() -> breaker.onSuccess(System.nanoTime() - started, TimeUnit.NANOSECONDS))
                    .doOnError(error -> breaker.onError(
                            System.nanoTime() - started, TimeUnit.NANOSECONDS, error));
        });
    }

    @PostMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<String>> chatStream(
            @RequestBody JsonNode body, ServerWebExchange exchange) {
        return guardedStream(identity(client.post()
                .uri("/api/chat/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body), exchange)
                .retrieve()
                .bodyToFlux(SSE_TYPE)
                .timeout(timeout));
    }

    @GetMapping("/runs/{runId}/stream")
    public Flux<ServerSentEvent<String>> resume(
            @PathVariable String runId, ServerWebExchange exchange) {
        return guardedStream(identity(client.get()
                .uri("/api/runs/{runId}/stream", runId)
                .accept(MediaType.TEXT_EVENT_STREAM), exchange)
                .retrieve()
                .bodyToFlux(SSE_TYPE)
                .timeout(timeout));
    }

    @GetMapping("/kb")
    public Mono<JsonNode> listKnowledgeBases(ServerWebExchange exchange) {
        return guarded(identity(client.get().uri("/api/kb"), exchange)
                .retrieve()
                .bodyToMono(JsonNode.class));
    }

    @PostMapping("/kb")
    public Mono<JsonNode> createKnowledgeBase(
            @RequestBody JsonNode body, ServerWebExchange exchange) {
        return guarded(identity(client.post()
                .uri("/api/kb")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body), exchange)
                .retrieve()
                .bodyToMono(JsonNode.class));
    }

    @PostMapping(value = "/kb/{kbId}/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public Mono<JsonNode> upload(
            @PathVariable String kbId,
            @RequestPart("file") org.springframework.http.codec.multipart.FilePart file,
            ServerWebExchange exchange) {
        MultipartBodyBuilder builder = new MultipartBodyBuilder();
        builder.asyncPart("file", file.content(), org.springframework.core.io.buffer.DataBuffer.class)
                .filename(file.filename());
        return guarded(identity(client.post()
                .uri("/api/kb/{kbId}/documents", kbId)
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(BodyInserters.fromMultipartData(builder.build())), exchange)
                .retrieve()
                .bodyToMono(JsonNode.class));
    }

    @GetMapping("/system/backends")
    public Mono<JsonNode> backends(ServerWebExchange exchange) {
        return guarded(identity(client.get().uri("/api/system/backends"), exchange)
                .retrieve()
                .bodyToMono(JsonNode.class));
    }

    @GetMapping("/audit")
    public Mono<JsonNode> audit(ServerWebExchange exchange) {
        return guarded(identity(client.get().uri("/api/audit"), exchange)
                .retrieve()
                .bodyToMono(JsonNode.class));
    }

    @GetMapping("/whoami")
    public Mono<Map<String, String>> whoami(ServerWebExchange exchange) {
        return Mono.just(Map.of(
                "tenant_id", tenant(exchange),
                "role", role(exchange),
                "user_id", user(exchange),
                "trace_id", trace(exchange)));
    }

    @GetMapping("/health")
    public Mono<Map<String, String>> health() {
        return Mono.just(Map.of("status", "ok", "service", "gateway"));
    }
}
