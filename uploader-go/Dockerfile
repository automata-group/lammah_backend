FROM golang:1.26-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o uploader main.go

FROM alpine:latest

WORKDIR /app
COPY --from=builder /app/uploader .

# Create uploads directory
RUN mkdir -p uploads/temp

EXPOSE 8080

CMD ["./uploader"]
