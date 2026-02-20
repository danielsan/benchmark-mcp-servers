use std::{env, net::SocketAddr, sync::Arc, time::Instant};

use mimalloc::MiMalloc;
#[global_allocator]
static GLOBAL: MiMalloc = MiMalloc;

use anyhow::Result;
use axum::{Json, Router, routing::get};
use chrono::Utc;
use reqwest::Client;
use rmcp::{
    ErrorData as McpError, ServerHandler,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::*,
    tool, tool_handler, tool_router,
    transport::streamable_http_server::{
        StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
    },
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::time::{Duration, sleep};

#[derive(Clone)]
struct BenchmarkRustServer {
    http_client: Arc<Client>,
    tool_router: ToolRouter<Self>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct FibonacciArgs {
    n: i64,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct FetchExternalDataArgs {
    endpoint: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ProcessJsonDataArgs {
    data: Value,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SimulateDatabaseQueryArgs {
    query: String,
    #[serde(default)]
    delay_ms: i64,
}

#[tool_router]
impl BenchmarkRustServer {
    fn new(http_client: Arc<Client>) -> Self {
        Self {
            http_client,
            tool_router: Self::tool_router(),
        }
    }

    #[tool(description = "Calcula o N-ésimo número de Fibonacci de forma recursiva")]
    async fn calculate_fibonacci(
        &self,
        Parameters(args): Parameters<FibonacciArgs>,
    ) -> Result<CallToolResult, McpError> {
        if !(0..=40).contains(&args.n) {
            return Err(McpError::invalid_params("n deve estar entre 0 e 40", None));
        }

        Ok(as_text_result(json!({
            "input": args.n,
            "result": fibonacci(args.n),
            "server_type": "rust"
        })))
    }

    #[tool(description = "Faz uma requisição HTTP GET para uma API externa")]
    async fn fetch_external_data(
        &self,
        Parameters(args): Parameters<FetchExternalDataArgs>,
    ) -> Result<CallToolResult, McpError> {
        let start = Instant::now();
        let payload = match self.http_client.get(&args.endpoint).send().await {
            Ok(response) => json!({
                "url": args.endpoint,
                "status_code": response.status().as_u16(),
                "response_time_ms": start.elapsed().as_millis() as u64,
                "server_type": "rust"
            }),
            Err(error) => json!({
                "url": args.endpoint,
                "status_code": 0,
                "response_time_ms": start.elapsed().as_millis() as u64,
                "error": error.to_string(),
                "server_type": "rust"
            }),
        };

        Ok(as_text_result(payload))
    }

    #[tool(description = "Recebe um JSON, valida e transforma (uppercase em campos string)")]
    async fn process_json_data(
        &self,
        Parameters(args): Parameters<ProcessJsonDataArgs>,
    ) -> Result<CallToolResult, McpError> {
        let Some(data) = args.data.as_object() else {
            return Err(McpError::invalid_params("data must be an object", None));
        };

        let original_keys = data.keys().cloned().collect::<Vec<_>>();
        let transformed_data = transform_strings(&args.data);

        Ok(as_text_result(json!({
            "original_keys": original_keys,
            "transformed_data": transformed_data,
            "server_type": "rust"
        })))
    }

    #[tool(description = "Simula uma query de banco de dados com delay configurável")]
    async fn simulate_database_query(
        &self,
        Parameters(args): Parameters<SimulateDatabaseQueryArgs>,
    ) -> Result<CallToolResult, McpError> {
        if !(0..=5000).contains(&args.delay_ms) {
            return Err(McpError::invalid_params(
                "delay_ms deve estar entre 0 e 5000",
                None,
            ));
        }

        sleep(Duration::from_millis(args.delay_ms as u64)).await;

        Ok(as_text_result(json!({
            "query": args.query,
            "delay_ms": args.delay_ms,
            "timestamp": Utc::now().to_rfc3339(),
            "server_type": "rust"
        })))
    }
}

#[tool_handler]
impl ServerHandler for BenchmarkRustServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            protocol_version: ProtocolVersion::V_2024_11_05,
            capabilities: ServerCapabilities::builder().enable_tools().build(),
            server_info: Implementation {
                name: "BenchmarkRustServer".to_string(),
                version: "1.0.0".to_string(),
                title: None,
                icons: None,
                website_url: None,
            },
            instructions: Some("Benchmark MCP server with four benchmark tools.".to_string()),
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let port = env::var("PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(8084);
    let addr = SocketAddr::from(([0, 0, 0, 0], port));

    let http_client = Arc::new(Client::new());
    let mcp_service: StreamableHttpService<BenchmarkRustServer, LocalSessionManager> =
        StreamableHttpService::new(
            move || Ok(BenchmarkRustServer::new(http_client.clone())),
            LocalSessionManager::default().into(),
            StreamableHttpServerConfig::default(),
        );

    let app = Router::new()
        .route("/health", get(health))
        .nest_service("/mcp", mcp_service);

    println!("Rust MCP server listening on port {port}");
    println!("MCP endpoint: http://localhost:{port}/mcp");

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn health() -> Json<Value> {
    Json(json!({ "status": "ok", "server_type": "rust" }))
}

fn as_text_result(payload: Value) -> CallToolResult {
    CallToolResult::success(vec![Content::text(payload.to_string())])
}

fn fibonacci(n: i64) -> i64 {
    if n <= 1 {
        return n;
    }
    let (mut a, mut b) = (0i64, 1i64);
    for _ in 2..=n {
        (a, b) = (b, a + b);
    }
    b
}

fn transform_strings(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let transformed = map
                .iter()
                .map(|(key, val)| (key.clone(), transform_strings(val)))
                .collect();
            Value::Object(transformed)
        }
        Value::Array(items) => Value::Array(items.iter().map(transform_strings).collect()),
        Value::String(text) => Value::String(text.to_uppercase()),
        _ => value.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fibonacci_returns_expected_value() {
        assert_eq!(fibonacci(10), 55);
    }

    #[test]
    fn transform_strings_uppercases_nested_values() {
        let input = json!({
            "name": "rust",
            "nested": {
                "label": "fast"
            },
            "array": ["a", "b"]
        });
        let output = transform_strings(&input);
        assert_eq!(output["name"], "RUST");
        assert_eq!(output["nested"]["label"], "FAST");
        assert_eq!(output["array"][0], "A");
    }
}
