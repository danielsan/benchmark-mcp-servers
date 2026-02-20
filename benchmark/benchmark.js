import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter, Rate } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

// ─── Configuration ───────────────────────────────────────────────────
const SERVER_URL = __ENV.SERVER_URL || 'http://localhost:8080/mcp';
const SERVER_NAME = __ENV.SERVER_NAME || 'unknown';
const FETCH_ENDPOINT = __ENV.FETCH_ENDPOINT || 'http://mock-api:1080/api';
const K6_DURATION = __ENV.K6_DURATION || '5m';

export const options = {
    stages: [
        { duration: '10s', target: 10 },   // ramp-up
        { duration: K6_DURATION, target: 10 },   // sustained load
        { duration: '10s', target: 0 },    // ramp-down
    ],
    thresholds: {
        'http_req_failed': ['rate<0.05'],
    },
};

// ─── Custom Metrics ──────────────────────────────────────────────────
const initDuration = new Trend('mcp_initialize_duration', true);
const toolsListDuration = new Trend('mcp_tools_list_duration', true);
const fibDuration = new Trend('mcp_fibonacci_duration', true);
const fetchDuration = new Trend('mcp_fetch_duration', true);
const jsonDuration = new Trend('mcp_json_process_duration', true);
const dbDuration = new Trend('mcp_db_query_duration', true);
const sessionDuration = new Trend('mcp_full_session_duration', true);
const mcpErrors = new Counter('mcp_errors');
const mcpRequests = new Counter('mcp_requests');
const mcpErrorRate = new Rate('mcp_error_rate');

// ─── Helpers ─────────────────────────────────────────────────────────
const BASE_HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
};

function parseBody(body) {
    /** Parse response body — handles JSON and SSE formats. */
    if (!body) return null;
    const text = body.trim();
    if (text.length === 0) return null;

    // Try plain JSON first
    if (text.startsWith('{') || text.startsWith('[')) {
        try { return JSON.parse(text); } catch (e) { /* fall through */ }
    }

    // SSE format — find data: line with JSON
    const lines = text.split('\n');
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('data:')) {
            const payload = trimmed.substring(5).trim();
            if (payload && payload.startsWith('{')) {
                try { return JSON.parse(payload); } catch (e) { /* continue */ }
            }
        }
    }

    return null;
}

function mcpPost(payload, sessionId) {
    /** Send JSON-RPC request with optional session ID. */
    const headers = Object.assign({}, BASE_HEADERS);
    if (sessionId) {
        headers['Mcp-Session-Id'] = sessionId;
    }
    const res = http.post(SERVER_URL, JSON.stringify(payload), {
        headers: headers,
        timeout: '15s',
    });
    mcpRequests.add(1);

    // Extract session ID from response headers
    const newSessionId = res.headers['Mcp-Session-Id'] || sessionId;
    const result = parseBody(res.body);

    return { res, result, sessionId: newSessionId };
}

function mcpSession(toolName, toolArgs) {
    /**
     * Execute a complete MCP session per the spec:
     * 1. initialize → get session ID
     * 2. notifications/initialized (with session ID)
     * 3. tools/call (with session ID)
     * 4. DELETE session (cleanup per spec)
     */
    const start = Date.now();

    // 1. Initialize
    const { res: initRes, result: initResult, sessionId } = mcpPost({
        jsonrpc: '2.0', id: 1, method: 'initialize',
        params: {
            protocolVersion: '2024-11-05',
            capabilities: {},
            clientInfo: { name: 'k6-bench', version: '1.0' },
        },
    }, null);

    // 2. notifications/initialized (uses session ID, expects 202 or empty response)
    const notifyHeaders = Object.assign({}, BASE_HEADERS);
    if (sessionId) notifyHeaders['Mcp-Session-Id'] = sessionId;
    http.post(SERVER_URL, JSON.stringify({
        jsonrpc: '2.0', method: 'notifications/initialized',
    }), { headers: notifyHeaders, timeout: '5s' });

    // 3. Tool call (uses session ID)
    const { res: callRes, result: callResult } = mcpPost({
        jsonrpc: '2.0', id: 2, method: 'tools/call',
        params: { name: toolName, arguments: toolArgs },
    }, sessionId);

    const totalDuration = Date.now() - start;

    // 4. Close session (DELETE per MCP spec — prevents server-side session leak)
    if (sessionId) {
        const delHeaders = { 'Mcp-Session-Id': sessionId };
        http.del(SERVER_URL, null, { headers: delHeaders, timeout: '2s', tags: { name: 'session_cleanup' } });
    }

    // Track errors
    const isError = !callResult || callResult.error || !callResult.result;
    if (isError) {
        mcpErrors.add(1);
        mcpErrorRate.add(true);
    } else {
        mcpErrorRate.add(false);
    }

    return { initRes, callRes, callResult, totalDuration, sessionId };
}

// ─── Tool definitions ────────────────────────────────────────────────
const TOOLS = [
    {
        name: 'calculate_fibonacci',
        args: { n: 10 },
        metric: fibDuration,
        checkName: 'fibonacci ok',
        check: (r) => {
            if (!r || !r.result || !r.result.content) return false;
            try { return JSON.parse(r.result.content[0].text).result === 55; }
            catch { return false; }
        },
    },
    {
        name: 'fetch_external_data',
        args: { endpoint: FETCH_ENDPOINT },
        metric: fetchDuration,
        checkName: 'fetch ok',
        check: (r) => r && r.result && r.result.content,
    },
    {
        name: 'process_json_data',
        args: { data: { name: 'benchmark', count: 42, nested: { key: 'value' } } },
        metric: jsonDuration,
        checkName: 'json_process ok',
        check: (r) => {
            if (!r || !r.result || !r.result.content) return false;
            try { return JSON.parse(r.result.content[0].text).transformed_data.name === 'BENCHMARK'; }
            catch { return false; }
        },
    },
    {
        name: 'simulate_database_query',
        args: { query: 'SELECT * FROM benchmarks', delay_ms: 10 },
        metric: dbDuration,
        checkName: 'db_query ok',
        check: (r) => r && r.result && r.result.content,
    },
];

// ─── Main Test Function ──────────────────────────────────────────────
export default function () {
    for (const tool of TOOLS) {
        const { initRes, callRes, callResult, totalDuration } = mcpSession(tool.name, tool.args);

        // Record metrics
        initDuration.add(initRes.timings.duration);
        tool.metric.add(callRes.timings.duration);
        sessionDuration.add(totalDuration);

        // Validate
        const checks = {};
        checks[tool.checkName] = tool.check;
        check(callResult, checks);
    }

    // Also benchmark tools/list in its own session
    {
        const { res: initRes, sessionId } = mcpPost({
            jsonrpc: '2.0', id: 1, method: 'initialize',
            params: {
                protocolVersion: '2024-11-05', capabilities: {},
                clientInfo: { name: 'k6-bench', version: '1.0' },
            },
        }, null);

        const notifyHeaders = Object.assign({}, BASE_HEADERS);
        if (sessionId) notifyHeaders['Mcp-Session-Id'] = sessionId;
        http.post(SERVER_URL, JSON.stringify({
            jsonrpc: '2.0', method: 'notifications/initialized',
        }), { headers: notifyHeaders, timeout: '5s' });

        const { res: listRes, result: listResult } = mcpPost({
            jsonrpc: '2.0', id: 2, method: 'tools/list', params: {},
        }, sessionId);

        toolsListDuration.add(listRes.timings.duration);
        check(listResult, { 'tools/list ok': (r) => r && r.result && r.result.tools });

        // Close session
        if (sessionId) {
            http.del(SERVER_URL, null, { headers: { 'Mcp-Session-Id': sessionId }, timeout: '2s', tags: { name: 'session_cleanup' } });
        }
    }

    sleep(0.1);
}

// ─── Custom Summary ──────────────────────────────────────────────────
export function handleSummary(data) {
    const outputPath = __ENV.OUTPUT_PATH || `results/${SERVER_NAME}_k6.json`;

    const summary = {
        server: SERVER_NAME,
        timestamp: new Date().toISOString(),
        config: { vus: 10, duration: K6_DURATION, server_url: SERVER_URL },
        http: {
            total_requests: getValue(data, 'http_reqs', 'count', 0),
            failed_requests: getValue(data, 'http_req_failed', 'passes', 0),
            rps: getValue(data, 'http_reqs', 'rate', 0),
            latency: {
                avg: getValue(data, 'http_req_duration', 'avg'),
                min: getValue(data, 'http_req_duration', 'min'),
                max: getValue(data, 'http_req_duration', 'max'),
                p50: getPercentile(data, 'http_req_duration', '50'),
                p90: getPercentile(data, 'http_req_duration', '90'),
                p95: getPercentile(data, 'http_req_duration', '95'),
                p99: getPercentile(data, 'http_req_duration', '99'),
            },
        },
        mcp: {
            total_mcp_requests: getValue(data, 'mcp_requests', 'count', 0),
            mcp_errors: getValue(data, 'mcp_errors', 'count', 0),
            error_rate: getValue(data, 'mcp_error_rate', 'rate', 0),
        },
        session: extractTrend(data, 'mcp_full_session_duration'),
        tools: {},
    };

    const toolMetrics = {
        'calculate_fibonacci': 'mcp_fibonacci_duration',
        'fetch_external_data': 'mcp_fetch_duration',
        'process_json_data': 'mcp_json_process_duration',
        'simulate_database_query': 'mcp_db_query_duration',
        '_initialize': 'mcp_initialize_duration',
        '_tools_list': 'mcp_tools_list_duration',
    };

    for (const [toolName, metricName] of Object.entries(toolMetrics)) {
        const t = extractTrend(data, metricName);
        if (t) summary.tools[toolName] = t;
    }

    return {
        [outputPath]: JSON.stringify(summary, null, 2),
        stdout: textSummary(data, { indent: '  ', enableColors: true }),
    };
}

function getValue(data, metric, key, fallback) {
    try { return data.metrics[metric].values[key]; } catch { return fallback; }
}

function getPercentile(data, metric, p) {
    try { return data.metrics[metric].values[`p(${p})`]; } catch { return null; }
}

function extractTrend(data, name) {
    try {
        const m = data.metrics[name];
        if (!m) return null;
        return {
            avg: m.values.avg, min: m.values.min, max: m.values.max,
            p50: m.values['p(50)'], p90: m.values['p(90)'],
            p95: m.values['p(95)'], p99: m.values['p(99)'],
            count: m.values.count,
        };
    } catch { return null; }
}
