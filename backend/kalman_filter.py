from typing import Optional


class KalmanPriceFilter:
    """Kalman filter for financial price noise reduction."""

    def __init__(self, q: float = 1e-5, r: float = 1e-2) -> None:
        self.q = q   # process noise (market randomness)
        self.r = r   # measurement noise (price feed noise)
        self.p = 1.0
        self.x: Optional[float] = None
        self.history: list[dict[str, float]] = []

    def update(self, price: float) -> dict[str, float]:
        if self.x is None:
            self.x = price
            result: dict[str, float] = {
                "kalman_price": price, "kalman_deviation": 0.0, "kalman_gain": 1.0,
            }
            self.history.append(result)
            return result

        p_pred = self.p + self.q
        k      = p_pred / (p_pred + self.r)
        self.x = self.x + k * (price - self.x)
        self.p = (1 - k) * p_pred

        result = {
            "kalman_price":     self.x,
            "kalman_deviation": price - self.x,
            "kalman_gain":      k,
        }
        self.history.append(result)
        if len(self.history) > 1000:
            self.history = self.history[-500:]
        return result

    def get_trend(self) -> dict[str, float]:
        if len(self.history) < 3:
            return {"kalman_trend": 0.0, "kalman_velocity": 0.0}
        recent: float   = self.history[-1]["kalman_price"]
        prev: float     = self.history[-3]["kalman_price"]
        velocity: float = recent - prev
        trend: float    = 1.0 if velocity > 1e-6 else (-1.0 if velocity < -1e-6 else 0.0)
        return {"kalman_trend": trend, "kalman_velocity": velocity}

    def reset(self) -> None:
        self.x = None
        self.p = 1.0
        self.history = []
