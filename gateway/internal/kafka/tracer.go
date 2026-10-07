// Package kafka provides a Kafka-backed TraceEmitter for the gateway.
// It serialises TraceSpan structs to JSON and writes them to the
// meridian.traces topic consumed by the eval pipeline.
package kafka

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	kafkago "github.com/segmentio/kafka-go"

	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

// Tracer implements proxy.TraceEmitter by writing JSON-encoded spans to Kafka.
type Tracer struct {
	writer *kafkago.Writer
	topic  string
}

// NewTracer creates a Tracer that writes to the given brokers and topic.
// The writer uses async batching with a 10 ms batch timeout for throughput;
// the gateway emits spans fire-and-forget in a goroutine so latency impact
// is zero even if Kafka is slow.
func NewTracer(brokers []string, topic string) *Tracer {
	w := &kafkago.Writer{
		Addr:         kafkago.TCP(brokers...),
		Topic:        topic,
		Balancer:     &kafkago.LeastBytes{},
		BatchTimeout: 10 * time.Millisecond,
		// RequiredAcks=1: leader ack is sufficient for tracing data.
		// Losing a span on leader failure is acceptable; blocking the gateway is not.
		RequiredAcks: kafkago.RequireOne,
		Async:        false, // we already call Emit in a goroutine
	}
	return &Tracer{writer: w, topic: topic}
}

// Emit serialises span to JSON and writes it to Kafka.
// Intended to be called in a goroutine — errors are logged but not propagated.
func (t *Tracer) Emit(ctx context.Context, span *models.TraceSpan) error {
	payload, err := json.Marshal(span)
	if err != nil {
		return fmt.Errorf("kafka tracer: marshal: %w", err)
	}

	err = t.writer.WriteMessages(ctx, kafkago.Message{
		Key:   []byte(span.TenantID + ":" + span.RouteID),
		Value: payload,
	})
	if err != nil {
		slog.Debug("kafka tracer: write failed", "error", err, "trace_id", span.TraceID)
		return fmt.Errorf("kafka tracer: write: %w", err)
	}
	return nil
}

// Close flushes buffered messages and closes the underlying writer.
func (t *Tracer) Close() error {
	return t.writer.Close()
}
