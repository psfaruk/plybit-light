package broker

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"

	"github.com/redis/go-redis/v9"
)

var rdb *redis.Client

func Init() {
	addr := os.Getenv("REDIS_URL")
	if addr == "" {
		addr = "localhost:6379"
	}
	rdb = redis.NewClient(&redis.Options{Addr: addr})
}

// Candle represents an OHLC candle.
type Candle struct {
	Epoch  int64   `json:"epoch"`
	Open   float64 `json:"open"`
	High   float64 `json:"high"`
	Low    float64 `json:"low"`
	Close  float64 `json:"close"`
	Volume float64 `json:"volume,omitempty"`
	Closed bool    `json:"closed"`
}

// Publish sends a candle to Redis pub/sub channel.
func Publish(ctx context.Context, symbol string, granularity int, c Candle) {
	if rdb == nil {
		return
	}
	data, err := json.Marshal(c)
	if err != nil {
		log.Printf("broker marshal error: %v", err)
		return
	}
	channel := fmt.Sprintf("candle:%s:%d", symbol, granularity)
	if err := rdb.Publish(ctx, channel, string(data)).Err(); err != nil {
		log.Printf("broker publish error: %v", err)
	}
}

// GapFreeCandle ensures no gap between candles.
func GapFreeCandle(c Candle, lastClose float64) Candle {
	if lastClose == 0 {
		return c
	}
	diff := c.Open - lastClose
	if diff < 0 {
		diff = -diff
	}
	if diff > lastClose*0.001 {
		c.Open = lastClose
		if c.Low > lastClose {
			c.Low = lastClose
		}
		if c.High < lastClose {
			c.High = lastClose
		}
	}
	return c
}
