package deriv

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"playbit/streamer/broker"
)

const wsURL = "wss://ws.binaryws.com/websockets/v3?app_id=%s"

var (
	appID = getEnv("DERIVE_APP_ID", "114229")
	token = getEnv("DERIVE_TOKEN",  "")
)

// ForexPairs lists all Deriv symbols to stream.
var ForexPairs = []string{
	"frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxUSDCHF",
	"frxUSDCAD", "frxAUDUSD", "frxNZDUSD",
	"frxEURGBP", "frxEURJPY", "frxGBPJPY", "frxAUDJPY",
	"frxEURCAD", "frxGBPCAD", "frxEURAUD", "frxGBPAUD",
	"R_10", "R_25", "R_50", "R_75", "R_100",
	"frxXAUUSD",
}

// Granularities to stream (seconds).
var Granularities = []int{60, 120, 300, 900, 3600, 14400, 86400}

type ohlcMsg struct {
	MsgType string `json:"msg_type"`
	OHLC    *struct {
		Symbol    string  `json:"symbol"`
		OpenTime  int64   `json:"open_time"`
		Open      string  `json:"open"`
		High      string  `json:"high"`
		Low       string  `json:"low"`
		Close     string  `json:"close"`
		Granularity int   `json:"granularity"`
	} `json:"ohlc"`
	Error *struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

// lastClose tracks last close price for gap-free building
var (
	lastClose   = map[string]map[int]float64{}
	lastCloseMu sync.Mutex
)

// StreamAll starts streaming all Deriv pairs.
func StreamAll(ctx context.Context) {
	for _, gran := range Granularities {
		for _, pair := range ForexPairs {
			go func(p string, g int) {
				for {
					if err := streamPair(ctx, p, g); err != nil {
						log.Printf("deriv stream %s %ds error: %v — retrying in 5s", p, g, err)
					}
					select {
					case <-ctx.Done():
						return
					case <-time.After(5 * time.Second):
					}
				}
			}(pair, gran)
			time.Sleep(50 * time.Millisecond) // rate limit
		}
	}
}

func streamPair(ctx context.Context, symbol string, granularity int) error {
	url := fmt.Sprintf(wsURL, appID)
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, url, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	// Authorize
	authMsg := map[string]string{"authorize": token}
	if err := conn.WriteJSON(authMsg); err != nil {
		return fmt.Errorf("auth write: %w", err)
	}
	var authResp map[string]interface{}
	if err := conn.ReadJSON(&authResp); err != nil {
		return fmt.Errorf("auth read: %w", err)
	}
	if _, ok := authResp["error"]; ok {
		return fmt.Errorf("auth error: %v", authResp["error"])
	}

	// Subscribe to candles
	subMsg := map[string]interface{}{
		"ticks_history": symbol,
		"subscribe":     1,
		"end":           "latest",
		"count":         1,
		"granularity":   granularity,
		"style":         "candles",
	}
	if err := conn.WriteJSON(subMsg); err != nil {
		return fmt.Errorf("subscribe write: %w", err)
	}

	conn.SetReadDeadline(time.Now().Add(120 * time.Second))

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

		var msg ohlcMsg
		if err := json.Unmarshal(raw, &msg); err != nil {
			continue
		}
		if msg.Error != nil {
			return fmt.Errorf("server error: %s", msg.Error.Message)
		}
		if msg.MsgType != "ohlc" || msg.OHLC == nil {
			continue
		}

		o := msg.OHLC
		c := broker.Candle{
			Epoch:       o.OpenTime,
			Open:        parseF(o.Open),
			High:        parseF(o.High),
			Low:         parseF(o.Low),
			Close:       parseF(o.Close),
			Granularity: o.Granularity,
		}

		lastCloseMu.Lock()
		if _, ok := lastClose[symbol]; !ok {
			lastClose[symbol] = map[int]float64{}
		}
		lc := lastClose[symbol][granularity]
		c   = broker.GapFreeCandle(c, lc)
		lastClose[symbol][granularity] = c.Close
		lastCloseMu.Unlock()

		// Determine if candle is closed (next epoch started)
		epochNow := time.Now().Unix()
		c.Closed = epochNow >= o.OpenTime+int64(granularity)

		broker.Publish(ctx, symbol, granularity, c)
	}
}

func parseF(s string) float64 {
	var f float64
	fmt.Sscanf(s, "%f", &f)
	return f
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
