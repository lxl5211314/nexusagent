package com.nexusagent.gateway.config;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Edge settings. {@code apiKeys} maps key -> tenant id; when empty, auth is
 * disabled so local development (and the smoke test) needs no setup.
 *
 * <p>{@code keyRoles} optionally narrows a key to a role; unknown or blank roles
 * fall back to {@code viewer} so a typo fails closed. {@code redisUrl} switches
 * the rate limiter from a per-instance bucket to a shared one.
 */
@ConfigurationProperties(prefix = "nexus.gateway")
public class GatewayProperties {

    private String pythonBaseUrl = "http://127.0.0.1:8017";
    private Map<String, String> apiKeys = new LinkedHashMap<>();
    private Map<String, String> keyRoles = new LinkedHashMap<>();
    private int rateLimitPerMinute = 120;
    private Duration upstreamTimeout = Duration.ofSeconds(120);
    private String redisUrl = "";
    private int rateLimitBurst = 0;
    private float circuitFailureRate = 50f;
    private int circuitSlidingWindow = 20;
    private Duration circuitOpenFor = Duration.ofSeconds(20);

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

    public Map<String, String> getKeyRoles() {
        return keyRoles;
    }

    public void setKeyRoles(Map<String, String> keyRoles) {
        this.keyRoles = keyRoles;
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

    public String getRedisUrl() {
        return redisUrl;
    }

    public void setRedisUrl(String redisUrl) {
        this.redisUrl = redisUrl;
    }

    public int getRateLimitBurst() {
        return rateLimitBurst;
    }

    public void setRateLimitBurst(int rateLimitBurst) {
        this.rateLimitBurst = rateLimitBurst;
    }

    public float getCircuitFailureRate() {
        return circuitFailureRate;
    }

    public void setCircuitFailureRate(float circuitFailureRate) {
        this.circuitFailureRate = circuitFailureRate;
    }

    public int getCircuitSlidingWindow() {
        return circuitSlidingWindow;
    }

    public void setCircuitSlidingWindow(int circuitSlidingWindow) {
        this.circuitSlidingWindow = circuitSlidingWindow;
    }

    public Duration getCircuitOpenFor() {
        return circuitOpenFor;
    }

    public void setCircuitOpenFor(Duration circuitOpenFor) {
        this.circuitOpenFor = circuitOpenFor;
    }
}
