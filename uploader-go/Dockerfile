FROM golang:1.22-alpine AS builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache gcc musl-dev

# Copy go mod files
COPY go.mod go.sum ./
RUN go mod download

# Copy source code
COPY . .

# Build the application
RUN go build -o uploader main.go

# Runtime stage
FROM alpine:latest

# Install runtime dependencies (ffmpeg and pdftotext)
RUN apk add --no-cache ffmpeg poppler-utils ca-certificates

WORKDIR /app

# Copy binary from builder
COPY --from=builder /app/uploader .

# Create uploads directory
RUN mkdir -p uploads/temp

# Expose port (default 8080)
EXPOSE 8080

CMD ["./uploader"]
