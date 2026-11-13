package com.nexusagent.gateway.web;

import com.fasterxml.jackson.databind.JsonNode;
import com.nexusagent.gateway.config.GatewayProperties;
import java.time.Duration;
import java.util.Map;
import org.springframework.core.ParameterizedTypeReference;
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

    public ProxyController(GatewayProperties properties) {
        this.client = WebClient.builder().baseUrl(properties.getPythonBaseUrl()).build();
        this.timeout = properties.getUpstreamTimeout();
    }

    private static String tenant(ServerWebExchange exchange) {
        Object value = exchange.getAttribute(ApiKeyFilter.TENANT_ATTRIBUTE);
        return value == null ? "local" : value.toString();
    }

    @PostMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<String>> chatStream(
            @RequestBody JsonNode body, ServerWebExchange exchange) {
        return client.post()
                .uri("/api/chat/stream")
                .header("X-Tenant-Id", tenant(exchange))
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(SSE_TYPE)
                .timeout(timeout);
    }

    @GetMapping("/runs/{runId}/stream")
    public Flux<ServerSentEvent<String>> resume(
            @PathVariable String runId, ServerWebExchange exchange) {
        return client.get()
                .uri("/api/runs/{runId}/stream", runId)
                .header("X-Tenant-Id", tenant(exchange))
                .accept(MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(SSE_TYPE)
                .timeout(timeout);
    }

    @GetMapping("/kb")
    public Mono<JsonNode> listKnowledgeBases(ServerWebExchange exchange) {
        return client.get()
                .uri("/api/kb")
                .header("X-Tenant-Id", tenant(exchange))
                .retrieve()
                .bodyToMono(JsonNode.class);
    }

    @PostMapping("/kb")
    public Mono<JsonNode> createKnowledgeBase(
            @RequestBody JsonNode body, ServerWebExchange exchange) {
        return client.post()
                .uri("/api/kb")
                .header("X-Tenant-Id", tenant(exchange))
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body)
                .retrieve()
                .bodyToMono(JsonNode.class);
    }

    @PostMapping(value = "/kb/{kbId}/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public Mono<JsonNode> upload(
            @PathVariable String kbId,
            @RequestPart("file") org.springframework.http.codec.multipart.FilePart file,
            ServerWebExchange exchange) {
        MultipartBodyBuilder builder = new MultipartBodyBuilder();
        builder.asyncPart("file", file.content(), org.springframework.core.io.buffer.DataBuffer.class)
                .filename(file.filename());
        return client.post()
                .uri("/api/kb/{kbId}/documents", kbId)
                .header("X-Tenant-Id", tenant(exchange))
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(BodyInserters.fromMultipartData(builder.build()))
                .retrieve()
                .bodyToMono(JsonNode.class);
    }

    @GetMapping("/system/backends")
    public Mono<JsonNode> backends(ServerWebExchange exchange) {
        return client.get()
                .uri("/api/system/backends")
                .header("X-Tenant-Id", tenant(exchange))
                .retrieve()
                .bodyToMono(JsonNode.class);
    }

    @GetMapping("/audit")
    public Mono<JsonNode> audit(ServerWebExchange exchange) {
        return client.get()
                .uri("/api/audit")
                .header("X-Tenant-Id", tenant(exchange))
                .retrieve()
                .bodyToMono(JsonNode.class);
    }

    @GetMapping("/whoami")
    public Mono<Map<String, String>> whoami(ServerWebExchange exchange) {
        return Mono.just(Map.of("tenant_id", tenant(exchange)));
    }

    @GetMapping("/health")
    public Mono<Map<String, String>> health() {
        return Mono.just(Map.of("status", "ok", "service", "gateway"));
    }
}
