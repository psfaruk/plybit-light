package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"playbit/streamer/binance"
	"playbit/streamer/broker"
	"playbit/streamer/deriv"
	"playbit/streamer/news"
)

func main() {
	log.Println("PLAYBIT Go Streamer starting…")

	broker.Init()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Health endpoint for Railway
	go func() {
		http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("ok"))
		})
		port := os.Getenv("PORT")
		if port == "" {
			port = "8080"
		}
		if err := http.ListenAndServe(":"+port, nil); err != nil {
			log.Printf("Health server error: %v", err)
		}
	}()

	// News calendar refresher
	news.StartRefresher(ctx)

	// Start Deriv streams
	log.Println("Starting Deriv WebSocket streams…")
	go deriv.StreamAll(ctx)

	// Start Binance streams
	log.Println("Starting Binance WebSocket streams…")
	go binance.StreamAll(ctx)

	// Wait for shutdown signal
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Println("Shutting down streamer…")
	cancel()
}
