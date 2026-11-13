package com.nexusagent.gateway.web;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.nexusagent.gateway.policy.FixedWindowRateLimiter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.reactive.AutoConfigureWebTestClient;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
@TestPropertySource(
        properties = {"nexus.gateway.api-keys.demo-key=demo-tenant", "nexus.gateway.rate-limit-per-minute=3"})
class GatewayAuthTest {

    @Autowired
    private WebTestClient client;

    @Autowired
    private FixedWindowRateLimiter limiter;

    @BeforeEach
    void resetLimiter() {
        limiter.reset();
    }

    @Test
    void healthDoesNotRequireAKey() {
        client.get().uri("/api/v1/health").exchange().expectStatus().isOk();
        client.get().uri("/actuator/health").exchange().expectStatus().isOk();
    }

    @Test
    void protectedRouteRejectsMissingKey() {
        client.get().uri("/api/v1/system/backends")
                .exchange()
                .expectStatus()
                .isUnauthorized()
                .expectBody()
                .jsonPath("$.error")
                .isEqualTo("invalid or missing X-API-Key");
    }

    @Test
    void knownKeyPassesAuthAndIsRateLimited() {
        for (int i = 0; i < 3; i++) {
            client.get().uri("/api/v1/health")
                    .header("X-API-Key", "demo-key")
                    .exchange()
                    .expectStatus()
                    .isOk();
        }
        client.get().uri("/api/v1/health")
                .header("X-API-Key", "demo-key")
                .exchange()
                .expectStatus()
                .isEqualTo(429);
    }

    @Test
    void tenantHeaderIsNormalised() {
        assertEquals("acme", ApiKeyFilter.normalizeTenant("acme"));
        assertEquals("local", ApiKeyFilter.normalizeTenant("  "));
        assertEquals("local", ApiKeyFilter.normalizeTenant(null));
        assertEquals("bad-tenant-", ApiKeyFilter.normalizeTenant("bad tenant!"));
        // sanitized value would start with a dash, which is not a legal id
        assertEquals("local", ApiKeyFilter.normalizeTenant("\"; DROP TABLE runs; --"));
        assertEquals(32, ApiKeyFilter.normalizeTenant("x".repeat(80)).length());
    }
}
