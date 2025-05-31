from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import stripe
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://feedsheild.com"],  # Allow local dev and deployed frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Stripe configuration
stripe.api_key = os.getenv("STRIPE_API_KEY")

# Pydantic models
class UserOut(BaseModel):
    id: int
    email: str
    instagram_connected: bool
    instagram_accounts: list[str] = []
    payment_history: list[dict] = []
    blocked_count: int = 0
    chart_data: list[dict] = []

class Token(BaseModel):
    access_token: str
    token_type: str

class SignUpData(BaseModel):
    email: str
    password: str

class PaymentRequest(BaseModel):
    amount: int
    description: str

class SubscriptionRequest(BaseModel):
    price_id: str

class PaymentRecord(BaseModel):
    amount: float
    package: str
    date: str

class ConfirmPaymentRequest(BaseModel):
    payment_intent_id: str

# JWT configuration
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Database configuration
DB_PATH = os.getenv("DB_PATH")

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)  # Increased timeout to 30 seconds
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    try:
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)
            logger.info(f"Created database directory: {db_dir}")

        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Users table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            logger.info("Creating 'users' table")
            cursor.execute('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    stripe_customer_id TEXT,
                    blocked_count INTEGER DEFAULT 0
                )
            ''')
        else:
            # Check and add blocked_count column if missing
            cursor.execute("PRAGMA table_info(users)")
            columns = [col["name"] for col in cursor.fetchall()]
            if "blocked_count" not in columns:
                logger.info("Adding 'blocked_count' column to 'users' table")
                cursor.execute("ALTER TABLE users ADD COLUMN blocked_count INTEGER DEFAULT 0")

        # Accounts table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'")
        if not cursor.fetchone():
            logger.info("Creating 'accounts' table")
            cursor.execute('''
                CREATE TABLE accounts (
                    user_id INTEGER,
                    username TEXT,
                    is_connected BOOLEAN DEFAULT FALSE,
                    PRIMARY KEY (user_id, username),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')

        # Payment history table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payment_history'")
        if not cursor.fetchone():
            logger.info("Creating 'payment_history' table")
            cursor.execute('''
                CREATE TABLE payment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount REAL NOT NULL,
                    package TEXT NOT NULL,
                    date TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')

        # Daily blocked counts table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_blocked_counts'")
        if not cursor.fetchone():
            logger.info("Creating 'daily_blocked_counts' table")
            cursor.execute('''
                CREATE TABLE daily_blocked_counts (
                    user_id INTEGER,
                    date TEXT NOT NULL,
                    blocked INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, date),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')

        conn.commit()
        logger.info("Database tables initialized successfully")
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database tables: {str(e)}")
        raise Exception(f"Failed to initialize database: {str(e)}")
    finally:
        conn.close()

def create_access_token(data: dict, expires_delta: timedelta = None):
    try:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        logger.debug(f"JWT token created for {data.get('sub')}")
        return encoded_jwt
    except Exception as e:
        logger.error(f"Error creating JWT token: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create token: {str(e)}")

def verify_token(token: str):
    try:
        if not token:
            logger.error("No token provided in Authorization header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No token provided",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("id")
        if user_id is None:
            logger.error("Invalid token: user_id not found in payload")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: user_id not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        logger.debug(f"Token verified for user_id: {user_id}")
        return user_id
    except JWTError as e:
        logger.error(f"JWT error during token verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_user_by_id(user_id: int, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        if not user:
            logger.error(f"User not found for user_id: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        logger.debug(f"User found: {user['email']}, ID: {user['id']}")
        return user
    except sqlite3.Error as e:
        logger.error(f"Database error in get_user_by_id: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def auto_block_accounts(user_id: int, db: sqlite3.Connection):
    try:
        cursor = db.cursor()
        cursor.execute("SELECT blocked_count FROM users WHERE id = ?", (user_id,))
        blocked_count = cursor.fetchone()["blocked_count"]
        today = datetime.utcnow().date().isoformat()

        # Check daily blocking limit (100 blocks in 10 minutes)
        cursor.execute(
            "SELECT blocked, datetime(date, 'unixepoch', 'localtime') as last_updated FROM daily_blocked_counts WHERE user_id = ? AND date = ?",
            (user_id, today)
        )
        daily_record = cursor.fetchone()
        daily_count = daily_record["blocked"] if daily_record else 0
        last_updated = daily_record["last_updated"] if daily_record else None

        if daily_count < 100:
            # Check if 10 minutes have passed since the last update
            if last_updated:
                last_time = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
                if (datetime.utcnow() - last_time).total_seconds() > 600:  # Reset after 10 minutes
                    daily_count = 0
                    cursor.execute(
                        "INSERT OR REPLACE INTO daily_blocked_counts (user_id, date, blocked) VALUES (?, ?, ?)",
                        (user_id, today, daily_count)
                    )

            if daily_count < 100:
                blocked_count += 1
                daily_count += 1
                cursor.execute(
                    "INSERT OR REPLACE INTO daily_blocked_counts (user_id, date, blocked) VALUES (?, ?, ?)",
                    (user_id, today, daily_count)
                )
                cursor.execute("UPDATE users SET blocked_count = ? WHERE id = ?", (blocked_count, user_id))
                db.commit()
                logger.info(f"Auto-blocked account for user_id: {user_id}. Total: {blocked_count}, Daily: {daily_count}")
    except sqlite3.Error as e:
        logger.error(f"Database error in auto_block_accounts: {str(e)}")

async def start_auto_blocking():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE EXISTS (SELECT 1 FROM payment_history WHERE user_id = users.id)")
            paid_users = cursor.fetchall()
            for user in paid_users:
                cursor.execute("SELECT is_connected FROM accounts WHERE user_id = ?", (user["id"],))
                is_connected = cursor.fetchone()
                if is_connected and is_connected["is_connected"]:
                    await auto_block_accounts(user["id"], conn)
            conn.close()
        except Exception as e:
            logger.error(f"Error in auto_blocking task: {str(e)}")
        await asyncio.sleep(6)  # Run every 6 seconds to allow ~100 blocks in 10 minutes

# Initialize database at startup
try:
    init_db()
except Exception as e:
    logger.error(f"Startup database initialization failed: {str(e)}")
    raise

@app.on_event("startup")
async def startup_event():
    logger.info("Application startup")
    asyncio.create_task(start_auto_blocking())

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Application shutdown")

@app.post("/signup")
async def signup(form_data: SignUpData, db: sqlite3.Connection = Depends(get_db)):
    try:
        logger.debug(f"Received signup request for email: {form_data.email}")
        cursor = db.cursor()
        cursor.execute("SELECT email FROM users WHERE email = ?", (form_data.email,))
        if cursor.fetchone():
            logger.warning(f"Email already registered: {form_data.email}")
            raise HTTPException(status_code=400, detail="Email already registered")
        
        hashed_password = pwd_context.hash(form_data.password)
        cursor.execute(
            "INSERT INTO users (email, password, stripe_customer_id, blocked_count) VALUES (?, ?, ?, ?)",
            (form_data.email, hashed_password, None, 0)
        )
        db.commit()
        cursor.execute("SELECT id FROM users WHERE email = ?", (form_data.email,))
        user = cursor.fetchone()
        logger.info(f"User created successfully: {form_data.email}, ID: {user['id']}")
        return {"message": "Signup successful"}
    except sqlite3.OperationalError as e:
        logger.error(f"Database error in signup: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in signup: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.post("/login", response_model=Token)
async def login(form_data: SignUpData, db: sqlite3.Connection = Depends(get_db)):
    try:
        logger.debug(f"Login attempt for email: {form_data.email}")
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (form_data.email,))
        user = cursor.fetchone()
        if not user or not pwd_context.verify(form_data.password, user["password"]):
            logger.error(f"Login failed for email: {form_data.email}. Incorrect email or password.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        logger.info(f"User found: {user['email']}, ID: {user['id']}")
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": form_data.email, "id": user["id"]}, expires_delta=access_token_expires
        )
        logger.info(f"Generated token for {form_data.email}")
        return {"access_token": access_token, "token_type": "bearer"}
    except sqlite3.OperationalError as e:
        logger.error(f"Database error in login: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in login: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Login error: {str(e)}")

@app.get("/dashboard", response_model=UserOut)
async def get_dashboard(token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    try:
        user_id = verify_token(token)
        db_user = get_user_by_id(user_id, db)
        cursor = db.cursor()

        # Fetch Instagram connection status and accounts
        cursor.execute("SELECT username, is_connected FROM accounts WHERE user_id = ?", (user_id,))
        accounts = cursor.fetchall()
        instagram_accounts = [row["username"] for row in accounts if row["username"]]
        is_connected = any(row["is_connected"] for row in accounts)

        # Fetch payment history
        cursor.execute("SELECT amount, package, date FROM payment_history WHERE user_id = ?", (user_id,))
        payment_history = [{"amount": row["amount"], "package": row["package"], "date": row["date"]} for row in cursor.fetchall()]
        logger.debug(f"Payment history for user_id {user_id}: {payment_history}")

        # Fetch blocked count
        cursor.execute("SELECT blocked_count FROM users WHERE id = ?", (user_id,))
        blocked_count = cursor.fetchone()["blocked_count"]

        # Fetch chart data (last 90 days)
        cursor.execute(
            "SELECT date, blocked FROM daily_blocked_counts WHERE user_id = ? AND date >= ?",
            (user_id, (datetime.utcnow() - timedelta(days=90)).date().isoformat())
        )
        chart_data = [
            {"name": row["date"], "blocked": row["blocked"]} for row in cursor.fetchall()
        ]

        return UserOut(
            id=db_user["id"],
            email=db_user["email"],
            instagram_connected=is_connected,
            instagram_accounts=instagram_accounts,
            payment_history=payment_history,
            blocked_count=blocked_count,
            chart_data=chart_data
        )
    except sqlite3.OperationalError as e:
        logger.error(f"Database error in get_dashboard: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in get_dashboard: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.post("/connect-instagram")
async def connect_instagram(token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    try:
        user_id = verify_token(token)
        cursor = db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO accounts (user_id, username, is_connected) VALUES (?, ?, ?)",
            (user_id, "@instagram_acc", True)
        )
        db.commit()
        logger.info(f"Instagram connected for user_id: {user_id}")
        return {"message": "Instagram connected successfully"}
    except sqlite3.OperationalError as e:
        logger.error(f"Database error in connect_instagram: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/create-payment-intent")
async def create_payment_intent(request: PaymentRequest, token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    try:
        user_id = verify_token(token)
        db_user = get_user_by_id(user_id, db)
        payment_intent = stripe.PaymentIntent.create(
            amount=request.amount,
            currency="usd",
            description=request.description,
            automatic_payment_methods={"enabled": True},
            customer=stripe.Customer.create(
                email=db_user["email"],
                name=db_user["email"].split('@')[0]
            ).id,
        )
        logger.info(f"Created PaymentIntent: {payment_intent.id} for user {db_user['email']}")
        return {"clientSecret": payment_intent.client_secret}
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/confirm-payment")
async def confirm_payment(request: ConfirmPaymentRequest, token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    try:
        logger.info(f"Received confirm-payment request with payment_intent_id: {request.payment_intent_id}")
        user_id = verify_token(token)
        db_user = get_user_by_id(user_id, db)
        payment_intent = stripe.PaymentIntent.retrieve(request.payment_intent_id)
        logger.info(f"PaymentIntent status: {payment_intent.status}, amount: {payment_intent.amount}, description: {payment_intent.description}")
        if payment_intent.status == "succeeded":
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO payment_history (user_id, amount, package, date) VALUES (?, ?, ?, ?)",
                (user_id, payment_intent.amount / 100.0, payment_intent.description, datetime.utcnow().isoformat())
            )
            db.commit()
            logger.info(f"Payment confirmed and recorded for user_id: {user_id}, PaymentIntent: {request.payment_intent_id}")
            return {"message": "Payment confirmed"}
        else:
            logger.warning(f"Payment not successful for PaymentIntent: {request.payment_intent_id}, status: {payment_intent.status}")
            raise HTTPException(status_code=400, detail=f"Payment not successful. Status: {payment_intent.status}")
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in confirm_payment: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except sqlite3.Error as e:
        logger.error(f"Database error in confirm_payment: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in confirm_payment: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.post("/create-subscription")
async def create_subscription(request: SubscriptionRequest, token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    try:
        user_id = verify_token(token)
        db_user = get_user_by_id(user_id, db)
        cursor = db.cursor()

        if not db_user["stripe_customer_id"]:
            customer = stripe.Customer.create(
                email=db_user["email"],
                name=db_user["email"].split('@')[0]
            )
            cursor.execute("UPDATE users SET stripe_customer_id = ? WHERE id = ?", (customer.id, user_id))
            db.commit()
            logger.info(f"Created Customer: {customer.id} for user {db_user['email']}")
        else:
            customer = stripe.Customer.retrieve(db_user["stripe_customer_id"])

        one_month_from_now = int((datetime.utcnow() + timedelta(days=30)).timestamp())
        subscription = stripe.Subscription.create(
            customer=customer.id,
            items=[{"price": request.price_id}],
            billing_cycle_anchor=one_month_from_now,
            proration_behavior="none",
            payment_behavior="default_incomplete",
        )
        logger.info(f"Created Subscription: {subscription.id} for user {db_user['email']}, starting on {one_month_from_now}")

        return {
            "clientSecret": subscription.latest_invoice.payment_intent.client_secret,
            "subscriptionId": subscription.id,
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in create_subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")