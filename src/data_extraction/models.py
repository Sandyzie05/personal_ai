from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class Charge(BaseModel):
    category: str
    amount: float
    description: Optional[str] = None


class PaymentRecord(BaseModel):
    date: str
    amount: float
    method: Optional[str] = None


class BillData(BaseModel):
    total_due: float
    bill_date: str
    due_date: str
    account_number: str
    charges: List[Charge]
    payment_history: List[PaymentRecord]
    bill_type: str = "t-mobile"
