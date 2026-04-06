"""
Predictor de Congestión basado en Machine Learning (Regresión Lineal Online)
"""

import time
import math
from typing import Optional
from dataclasses import dataclass
from collections import deque
import threading

@dataclass
class TrafficSample:
    timestamp: float
    throughput_bps: float
    latency_us: float
    queue_depth: int
    packet_loss_rate: float

@dataclass
class PredictionResult:
    congestion_probability: float
    time_to_congestion_ms: float
    recommended_batch_size: int
    recommended_priority: int
    flow_control_enabled: bool

class OnlineLinearRegressor:
    """Regresión lineal online con gradient descent."""
    
    def __init__(self, learning_rate: float = 0.01):
        self.lr = learning_rate
        self.weights = [0.0] * 5
        self.bias = 0.0
    
    def predict(self, features):
        pred = self.bias
        for w, x in zip(self.weights, features):
            pred += w * x
        return pred
    
    def update(self, features, target):
        error = self.predict(features) - target
        self.bias -= self.lr * error
        for i in range(len(self.weights)):
            self.weights[i] -= self.lr * error * features[i]

class CongestionPredictor:
    def __init__(self):
        self.samples = deque(maxlen=100)
        self.regressor = OnlineLinearRegressor()
        self.lock = threading.Lock()
        self.latency_threshold = 1000.0  # us
    
    def add_sample(self, throughput, latency, queue, loss):
        with self.lock:
            self.samples.append(TrafficSample(time.time(), throughput, latency, queue, loss))
    
    def predict(self) -> Optional[PredictionResult]:
        with self.lock:
            if len(self.samples) < 10:
                return None
            
            recent = list(self.samples)[-10:]
            lat_trend = (recent[-1].latency_us - recent[0].latency_us) / len(recent)
            
            features = [
                lat_trend / 100.0,
                recent[-1].queue_depth / 100.0,
                recent[-1].packet_loss_rate,
                0.0,  # jitter
                0.0   # reserved
            ]
            
            score = self.regressor.predict(features)
            prob = 1.0 / (1.0 + math.exp(-score))
            
            batch = 16 if prob < 0.5 else 4
            priority = 80 if prob > 0.8 else 50
            
            self.regressor.update(features, 1.0 if recent[-1].latency_us > self.latency_threshold else 0.0)
            
            return PredictionResult(prob, 50.0, batch, priority, prob > 0.7)

predictor = CongestionPredictor()