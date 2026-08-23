"""
SQLite storage for extracted bill data with indexed queries.
"""

import sqlite3
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from .models import BillData, Charge, PaymentRecord


class BillStorage:
    """Stores and queries extracted bill data in SQLite."""
    
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or "~/.personal_ai/bills.db").expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._initialize_schema()
    
    def _initialize_schema(self) -> None:
        """Create tables with indexes for fast queries."""
        cursor = self.conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_number TEXT NOT NULL,
                bill_date TEXT NOT NULL,
                due_date TEXT NOT NULL,
                total_due REAL NOT NULL,
                bill_type TEXT DEFAULT 't-mobile',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_hash TEXT UNIQUE
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_account ON bills(account_number)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bill_date ON bills(bill_date)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_due_date ON bills(due_date)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_total_due ON bills(total_due)
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS charges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                description TEXT,
                FOREIGN KEY (bill_id) REFERENCES bills(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_charges_bill_id ON charges(bill_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_charges_category ON charges(category)
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL,
                payment_date TEXT NOT NULL,
                amount REAL NOT NULL,
                method TEXT,
                FOREIGN KEY (bill_id) REFERENCES bills(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_bill_id ON payments(bill_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date)
        """)
        
        self.conn.commit()
    
    def store_bill(self, bill_data: BillData, file_hash: str = None) -> int:
        """
        Store bill data and return bill ID.
        
        Args:
            bill_data: BillData model with extracted information
            file_hash: Optional hash for duplicate detection
            
        Returns:
            Database ID of stored bill
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            INSERT INTO bills 
            (account_number, bill_date, due_date, total_due, bill_type, file_hash)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            bill_data.account_number,
            bill_data.bill_date,
            bill_data.due_date,
            bill_data.total_due,
            bill_data.bill_type,
            file_hash
        ))
        
        bill_id = cursor.lastrowid
        
        for charge in bill_data.charges:
            cursor.execute("""
                INSERT INTO charges 
                (bill_id, category, amount, description)
                VALUES (?, ?, ?, ?)
            """, (
                bill_id,
                charge.category,
                charge.amount,
                charge.description
            ))
        
        for payment in bill_data.payment_history:
            cursor.execute("""
                INSERT INTO payments 
                (bill_id, payment_date, amount, method)
                VALUES (?, ?, ?, ?)
            """, (
                bill_id,
                payment.date,
                payment.amount,
                payment.method
            ))
        
        self.conn.commit()
        return bill_id
    
    def get_bill_by_id(self, bill_id: int) -> Optional[BillData]:
        """Retrieve bill by ID."""
        cursor = self.conn.cursor()
        
        bill_row = cursor.execute(
            "SELECT * FROM bills WHERE id = ?", (bill_id,)
        ).fetchone()
        
        if not bill_row:
            return None
        
        charges = cursor.execute(
            "SELECT * FROM charges WHERE bill_id = ?", (bill_id,)
        ).fetchall()
        
        payments = cursor.execute(
            "SELECT * FROM payments WHERE bill_id = ?", (bill_id,)
        ).fetchall()
        
        return BillData(
            total_due=bill_row['total_due'],
            bill_date=bill_row['bill_date'],
            due_date=bill_row['due_date'],
            account_number=bill_row['account_number'],
            charges=[Charge(
                category=r['category'],
                amount=r['amount'],
                description=r['description']
            ) for r in charges],
            payment_history=[PaymentRecord(
                date=r['payment_date'],
                amount=r['amount'],
                method=r['method']
            ) for r in payments]
        )
    
    def get_bills_by_account(self, account_number: str) -> List[BillData]:
        """Retrieve all bills for an account."""
        cursor = self.conn.cursor()
        
        bill_rows = cursor.execute(
            "SELECT * FROM bills WHERE account_number = ? ORDER BY bill_date DESC",
            (account_number,)
        ).fetchall()
        
        return [self._row_to_bill(r) for r in bill_rows]
    
    def get_bills_by_date_range(
        self, start_date: str, end_date: str
    ) -> List[BillData]:
        """Retrieve bills within date range."""
        cursor = self.conn.cursor()
        
        bill_rows = cursor.execute(
            """SELECT * FROM bills 
            WHERE bill_date BETWEEN ? AND ? 
            ORDER BY bill_date DESC""",
            (start_date, end_date)
        ).fetchall()
        
        return [self._row_to_bill(r) for r in bill_rows]
    
    def get_total_spent(self, account_number: str = None) -> float:
        """Get total amount spent on bills."""
        cursor = self.conn.cursor()
        
        if account_number:
            cursor.execute(
                "SELECT SUM(total_due) FROM bills WHERE account_number = ?",
                (account_number,)
            )
        else:
            cursor.execute("SELECT SUM(total_due) FROM bills")
        
        result = cursor.fetchone()[0]
        return result or 0.0
    
    def _row_to_bill(self, row: sqlite3.Row) -> BillData:
        """Convert database row to BillData model."""
        cursor = self.conn.cursor()
        
        charges = cursor.execute(
            "SELECT * FROM charges WHERE bill_id = ?", (row['id'],)
        ).fetchall()
        
        payments = cursor.execute(
            "SELECT * FROM payments WHERE bill_id = ?", (row['id'],)
        ).fetchall()
        
        return BillData(
            total_due=row['total_due'],
            bill_date=row['bill_date'],
            due_date=row['due_date'],
            account_number=row['account_number'],
            charges=[Charge(
                category=r['category'],
                amount=r['amount'],
                description=r['description']
            ) for r in charges],
            payment_history=[PaymentRecord(
                date=r['payment_date'],
                amount=r['amount'],
                method=r['method']
            ) for r in payments]
        )
    
    def delete_bill(self, bill_id: int) -> bool:
        """Delete a bill and its related data."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
