package news

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"sync"
	"time"
)

const calendarURL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

type Event struct {
	Date     string `json:"date"`
	Time     string `json:"time"`
	Currency string `json:"country"`
	Impact   string `json:"impact"`
	Title    string `json:"title"`
}

var (
	cached   []Event
	cachedMu sync.RWMutex
	lastFetch time.Time
)

// StartRefresher fetches the news calendar on a 1-hour schedule.
func StartRefresher(ctx context.Context) {
	refresh()
	ticker := time.NewTicker(1 * time.Hour)
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				refresh()
			}
		}
	}()
}

func refresh() {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, calendarURL, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("news fetch error: %v", err)
		return
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	var events []Event
	if err := json.Unmarshal(body, &events); err != nil {
		log.Printf("news parse error: %v", err)
		return
	}

	cachedMu.Lock()
	cached    = events
	lastFetch = time.Now()
	cachedMu.Unlock()
	log.Printf("news: loaded %d events", len(events))
}

// IsHighImpact returns true if the pair has a high-impact event within ±15 minutes.
func IsHighImpact(pair string, t time.Time) bool {
	cachedMu.RLock()
	events := cached
	cachedMu.RUnlock()

	window := 15 * time.Minute
	for _, e := range events {
		if e.Impact != "High" {
			continue
		}
		evTime, err := parseTime(e.Date, e.Time)
		if err != nil {
			continue
		}
		diff := t.Sub(evTime)
		if diff < 0 {
			diff = -diff
		}
		if diff <= window {
			if isPairAffected(pair, e.Currency) {
				return true
			}
		}
	}
	return false
}

func parseTime(date, t string) (time.Time, error) {
	layouts := []string{
		"2006-01-02 3:04pm",
		"2006-01-02 3:04am",
		"2006-01-02 15:04",
	}
	combined := fmt.Sprintf("%s %s", date, t)
	for _, l := range layouts {
		if parsed, err := time.Parse(l, combined); err == nil {
			return parsed.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("cannot parse %q %q", date, t)
}

func isPairAffected(pair, currency string) bool {
	if len(pair) < 6 {
		return false
	}
	c1 := pair[:3]
	c2 := pair[3:6]
	return c1 == currency || c2 == currency || currency == "USD"
}
