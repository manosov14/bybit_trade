from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any
from usecases.order_manager import OrderManager, OrderPlan

@dataclass
class PlanEmitter:
    env_path: str = '.env'
    mode: str = 'test'

    def __post_init__(self):
        self.om = OrderManager(live=(self.mode=='live'), env_path=self.env_path)

    def emit(self, plan: OrderPlan):
        # Thin wrapper: delegate to OrderManager.place(plan)
        return self.om.place(plan)
