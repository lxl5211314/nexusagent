package com.nexusagent.gateway.policy;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class FixedWindowRateLimiterTest {

    @Test
    void allowsUpToTheLimitThenRejects() {
        FixedWindowRateLimiter limiter = new FixedWindowRateLimiter();

        for (int i = 0; i < 3; i++) {
            assertThat(limiter.allow("tenant-a", 3)).isTrue();
        }
        assertThat(limiter.allow("tenant-a", 3)).isFalse();
        assertThat(limiter.allow("tenant-a", 3)).isFalse();
    }

    @Test
    void tenantsHaveIndependentBudgets() {
        FixedWindowRateLimiter limiter = new FixedWindowRateLimiter();

        assertThat(limiter.allow("tenant-a", 1)).isTrue();
        assertThat(limiter.allow("tenant-b", 1)).isTrue();
        assertThat(limiter.allow("tenant-a", 1)).isFalse();
    }

    @Test
    void resetClearsEveryWindow() {
        FixedWindowRateLimiter limiter = new FixedWindowRateLimiter();
        assertThat(limiter.allow("tenant-a", 1)).isTrue();
        assertThat(limiter.allow("tenant-a", 1)).isFalse();

        limiter.reset();

        assertThat(limiter.allow("tenant-a", 1)).isTrue();
    }
}
