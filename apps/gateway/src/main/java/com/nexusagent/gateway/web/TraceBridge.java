package com.nexusagent.gateway.web;

/**
 * Reads the trace id from the SkyWalking tracer when the agent is attached.
 *
 * <p>The toolkit is an optional runtime dependency: without {@code -javaagent}
 * the class is still on the classpath but {@code TraceContext.traceId()} returns
 * an empty string, and without the jar it is not resolvable at all. Reflection
 * keeps both cases from breaking startup, and the caller falls back to its own
 * id.
 */
final class TraceBridge {

    private static final String TRACE_CONTEXT = "org.apache.skywalking.apm.toolkit.trace.TraceContext";

    private TraceBridge() {}

    /** The tracer's id, or {@code null} when no agent is running. */
    static String currentTraceId() {
        try {
            Class<?> type = Class.forName(TRACE_CONTEXT);
            Object value = type.getMethod("traceId").invoke(null);
            if (value instanceof String text && !text.isBlank()) {
                return text;
            }
        } catch (ReflectiveOperationException | LinkageError | RuntimeException ignored) {
            // No agent, no toolkit, or the tracer is not active in this thread.
        }
        return null;
    }
}
