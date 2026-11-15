package com.nexusagent.gateway.policy;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class TokenBucketTest {

    @Test
    void burstIsSpentThenRejected() {
        LocalTokenBucketRateLimiter limiter = new LocalTokenBucketRateLimiter();
        for (int i = 0; i < 5; i++) {
            assertThat(limiter.tryAcquire("t", 60, 5)).isEqualTo(-1);
        }
        assertThat(limiter.tryAcquire("t", 60, 5)).isGreaterThanOrEqualTo(1);
    }

    @Test
    void bucketsArePerTenant() {
        LocalTokenBucketRateLimiter limiter = new LocalTokenBucketRateLimiter();
        assertThat(limiter.tryAcquire("a", 1, 1)).isEqualTo(-1);
        assertThat(limiter.tryAcquire("a", 1, 1)).isPositive();
        assertThat(limiter.tryAcquire("b", 1, 1)).isEqualTo(-1);
    }

    @Test
    void retryAfterShrinksAsTokensRefill() throws InterruptedException {
        LocalTokenBucketRateLimiter limiter = new LocalTokenBucketRateLimiter();
        limiter.tryAcquire("r", 600, 1);
        long first = limiter.tryAcquire("r", 600, 1);
        assertThat(first).isPositive();
        Thread.sleep(200);
        long second = limiter.tryAcquire("r", 600, 1);
        assertThat(second).isLessThanOrEqualTo(first);
    }

    @Test
    void resetClearsState() {
        LocalTokenBucketRateLimiter limiter = new LocalTokenBucketRateLimiter();
        limiter.tryAcquire("x", 1, 1);
        assertThat(limiter.tryAcquire("x", 1, 1)).isPositive();
        limiter.reset();
        assertThat(limiter.tryAcquire("x", 1, 1)).isEqualTo(-1);
    }
}
