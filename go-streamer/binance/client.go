package binance

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"playbit/streamer/broker"
)

// CryptoPairs to stream from Binance.
var CryptoPairs = []string{"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}

// Granularities mapped to Binance interval strings.
var granularityMap = map[int]string{
	60: "1m", 300: "5m", 900: "15m", 3600: "1h",
}

var (
	lastClose   = map[string]map[int]float64{}
	lastCloseMu sync.Mutex
)

type klineEvent struct {
	EventType string `json:"e"`
	Kline     struct {
		OpenTime  int64  `json:"t"`
		Open      string `json:"o"`
		High      string `json:"h"`
		Low       string `json:"l"`
		Close     string `json:"c"`
		Volume    string `json:"v"`
		IsClosed  bool   `json:"x"`
		Interval  string `json:"i"`
	} `json:"k"`
}

// StreamAll starts streaming all Binance crypto pairs.
func StreamAll(ctx context.Context) {
	for _, gran := range []int{60, 300, 900, 3600} {
		interval, ok := granularityMap[gran]
		if !ok {
			continue
		}
		for _, pair := range CryptoPairs {
			go func(p string, g int, iv string) {
				for {
					if err := streamPair(ctx, p, g, iv); err != nil {
						log.Printf("binance stream %s %ds error: %v — retrying in 5s", p, g, err)
					}
					select {
					case <-ctx.Done():
						return
					case <-time.After(5 * time.Second):
					}
				}
			}(pair, gran, interval)
			time.Sleep(100 * time.Millisecond)
		}
	}
}

func streamPair(ctx context.Context, symbol string, granularity int, interval string) error {
	stream := fmt.Sprintf("%s@kline_%s", lower(symbol), interval)
	url    := fmt.Sprintf("wss://stream.binance.com:9443/ws/%s", stream)

	conn, _, err := websocket.DefaultDialer.DialContext(ctx, url, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		conn.SetReadDeadline(time.Now().Add(120 * time.Second))
		_, raw, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}

		var evt klineEvent
		if err := json.Unmarshal(raw, &evt); err != nil {
			continue
		}
		if evt.EventType != "kline" {
			continue
		}

		k := evt.Kline
		c := broker.Candle{
			Epoch:  k.OpenTime / 1000,
			Open:   parseF(k.Open),
			High:   parseF(k.High),
			Low:    parseF(k.Low),
			Close:  parseF(k.Close),
			Volume: parseF(k.Volume),
			Closed: k.IsClosed,
		}

		lastCloseMu.Lock()
		if _, ok := lastClose[symbol]; !ok {
			lastClose[symbol] = map[int]float64{}
		}
		lc := lastClose[symbol][granularity]
		c   = broker.GapFreeCandle(c, lc)
		lastClose[symbol][granularity] = c.Close
		lastCloseMu.Unlock()

		broker.Publish(ctx, symbol, granularity, c)
	}
}

func parseF(s string) float64 {
	var f float64
	fmt.Sscanf(s, "%f", &f)
	return f
}

func lower(s string) string {
	result := make([]byte, len(s))
	for i := range s {
		if s[i] >= 'A' && s[i] <= 'Z' {
			result[i] = s[i] + 32
		} else {
			result[i] = s[i]
		}
	}
	return string(result)
}
