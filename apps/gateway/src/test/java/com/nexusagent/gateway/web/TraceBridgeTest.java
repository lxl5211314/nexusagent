package com.nexusagent.gateway.web;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class TraceBridgeTest {

    @Test
    void returnsNullWhenNoAgentIsAttached() {
        // The toolkit jar is on the classpath but there is no -javaagent in a unit
        // test, so the tracer is inactive and the filter must keep its own id.
        assertThat(TraceBridge.currentTraceId()).isNull();
    }
}
