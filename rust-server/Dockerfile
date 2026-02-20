FROM rust:1.93-alpine AS builder

WORKDIR /app

COPY Cargo.toml Cargo.lock ./
COPY src ./src

RUN cargo build --release

FROM alpine:3.20

RUN apk --no-cache add ca-certificates

WORKDIR /app

COPY --from=builder /app/target/release/mcp-rust-server /app/server

EXPOSE 8084

CMD ["/app/server"]
