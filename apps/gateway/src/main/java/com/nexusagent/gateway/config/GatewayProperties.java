package com.nexusagent.gateway.config;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Edge settings. {@code apiKeys} maps key -> tenant id; when empty, auth is
 * disabled so local development (and the smoke test) needs no setup.
 */
@ConfigurationProperties(prefix = "nexus.gateway")
public class GatewayProperties {

    private String pythonBaseUrl = "http://127.0.0.1:8017";
    private Map<String, String> apiKeys = new LinkedHashMap<>();
    private int rateLimitPerMinute = 120;
    private Duration upstreamTimeout = Duration.ofSeconds(120);

    public String getPythonBaseUrl() {
        return pythonBaseUrl;
    }

    public void setPythonBaseUrl(String pythonBaseUrl) {
        this.pythonBaseUrl = pythonBaseUrl;
    }

    public Map<String, String> getApiKeys() {
        return apiKeys;
    }

    public void setApiKeys(Map<String, String> apiKeys) {
        this.apiKeys = apiKeys;
    }

    public int getRateLimitPerMinute() {
        return rateLimitPerMinute;
    }

    public void setRateLimitPerMinute(int rateLimitPerMinute) {
        this.rateLimitPerMinute = rateLimitPerMinute;
    }

    public Duration getUpstreamTimeout() {
        return upstreamTimeout;
    }

    public void setUpstreamTimeout(Duration upstreamTimeout) {
        this.upstreamTimeout = upstreamTimeout;
    }
}
