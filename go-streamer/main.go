package main

import (
	"context"
	"log"
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
