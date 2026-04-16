from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify)
import mysql.connector, bcrypt, re, uuid, os, string, random, base64
from functools import wraps
from datetime import datetime, date, timedelta
try:
    import requests as req_lib
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ─────────────────────────────────────────────────────────────
#  APP CONFIG
# ─────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")

# ─────────────────────────────────────────────────────────────
#  DATABASE CONFIG
# ─────────────────────────────────────────────────────────────
DB_CONFIG = {
    'host':     'localhost',
    'user':     'root',
    'password': '',          # ← your MySQL password
    'database': 'tickit_db',
    'charset':  'utf8mb4',
    'autocommit': False,
}

def get_db():
    """Get a database connection."""
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as e:
        print(f"Database connection error: {e}")
        raise

def query(db, sql, params=(), one=False):
    """Execute a SELECT query and return results."""
    cur = db.cursor(dictionary=True)
    cur.execute(sql, params)
    result = cur.fetchone() if one else cur.fetchall()
    cur.close()
    return result

def execute(db, sql, params=()):
    """Execute an INSERT/UPDATE/DELETE query and return lastrowid."""
    cur = db.cursor()
    cur.execute(sql, params)
    last_id = cur.lastrowid
    cur.close()
    return last_id

# ─────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────
# Seat pricing by category (base prices)
SEAT_PRICES = {'VIP': 650, 'Standard': 450, 'Regular': 450, 'PWD': 450}
# Ticket types (Regular only - no discounts)
TICKET_DISCOUNTS = {'Regular': 1.0}
# Flat lookup used for legacy/simple display (Regular Standard price)
TICKET_PRICES  = {'Regular': 450, 'Student': 350, 'Senior / PWD': 360}
ADMIN_EMAIL    = 'admin@gmail.com'
ADMIN_PASSWORD = 'admin12345'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Payment simulation weights  (must sum to 1.0)
PAY_SUCCESS_RATE = 0.80
PAY_FAILED_RATE  = 0.15
# PAY_PENDING_RATE = 0.05  (remainder)

# ─── PayMongo TEST MODE ───────────────────────────────────────────────────────
# Replace these with your PayMongo TEST keys from https://dashboard.paymongo.com
# Use TEST keys only — never commit live keys to source control.
# 
# To set up PayMongo test mode:
# 1. Create account at https://dashboard.paymongo.com
# 2. Generate TEST API keys (Secret Key and Public Key)
# 3. Set environment variables:
#    export PAYMONGO_SECRET_KEY="pk_test_..."
#    export PAYMONGO_PUBLIC_KEY="pk_test..."
# 4. Test with these card numbers:
#    - Visa: 4343 4343 4343 4343 (any future expiry + any CVC)
#    - Mastercard: 5555 5555 5555 4444
#    If PAYMONGO_SECRET_KEY is not set, system falls back to payment simulation.
PAYMONGO_SECRET_KEY = ""
PAYMONGO_PUBLIC_KEY = ""
PAYMONGO_BASE_URL   = 'https://api.paymongo.com/v1'

# For LOCAL TESTING: Use mock mode even with keys configured
# Set to False to use real PayMongo, True to use mock checkout for testing
USE_MOCK_MODE = False
USE_PAYMONGO = bool(PAYMONGO_SECRET_KEY) and REQUESTS_AVAILABLE and not USE_MOCK_MODE

def _paymongo_auth():
    return base64.b64encode(f'{PAYMONGO_SECRET_KEY}:'.encode()).decode()

def create_paymongo_link(amount_centavos, description, ref_code, success_url):
    """Create a PayMongo Payment Link (test mode). Returns (link_id, checkout_url) or raises."""
    
    # ✅ MOCK MODE: If no real keys, use local mock
    if not USE_PAYMONGO:
        db = get_db()
        try:
            link_id = 'MOCK-' + uuid.uuid4().hex[:20].upper()
            checkout_url = url_for('paymongo_mock_checkout', link_id=link_id, ref=ref_code, amount=amount_centavos, _external=True)
            
            # Store mock link in database
            execute(db, """
                INSERT INTO paymongo_mock_links (link_id, ref_code, amount, description, status, created_at)
                VALUES (%s, %s, %s, %s, 'unpaid', NOW())
                ON DUPLICATE KEY UPDATE status='unpaid'
            """, (link_id, ref_code, amount_centavos / 100, description))
            db.commit()
            db.close()
            
            print(f"[OK] MOCK PayMongo link created: {link_id}")
            return link_id, checkout_url
        except Exception as e:
            db.close()
            print(f"[ERROR] Mock link creation failed: {str(e)}")
            raise
    
    # REAL MODE: Use actual PayMongo API
    headers = {
        'Authorization': f'Basic {_paymongo_auth()}',
        'Content-Type':  'application/json',
        'Accept':        'application/json',
    }
    body = {
        'data': {
            'attributes': {
                'amount':      amount_centavos,
                'description': description,
                'remarks':     ref_code,
                'redirect_url': success_url,  # Important: Tell PayMongo where to redirect after payment
            }
        }
    }
    resp = req_lib.post(f'{PAYMONGO_BASE_URL}/links', headers=headers,
                        json=body, timeout=20)
    if resp.status_code not in (200, 201):
        raise Exception(f'PayMongo error {resp.status_code}: {resp.text}')
    data = resp.json().get('data', {})
    attrs = data.get('attributes', {})
    link_id = data.get('id', '')
    checkout_url = attrs.get('checkout_url', '')
    
    print(f"[OK] Real PayMongo link created: {link_id}")
    return link_id, checkout_url

def verify_paymongo_link(link_id):
    """Fetch a PayMongo link and return its status ('unpaid', 'paid', etc.)."""
    # ✅ MOCK MODE: Check if this is a mock link (starts with MOCK-)
    if link_id.startswith('MOCK-'):
        try:
            db = get_db()
            result = query(db, "SELECT status FROM paymongo_mock_links WHERE link_id=%s", (link_id,), one=True)
            db.close()
            if result:
                return result['status']
        except:
            pass
        return 'unknown'
    
    # REAL MODE: Use actual PayMongo API (if keys configured)
    if not USE_PAYMONGO:
        return 'unknown'
    
    headers = {'Authorization': f'Basic {_paymongo_auth()}', 'Accept': 'application/json'}
    resp = req_lib.get(f'{PAYMONGO_BASE_URL}/links/{link_id}', headers=headers, timeout=15)
    if resp.status_code != 200:
        print(f"[DEBUG] PayMongo link fetch error: {resp.status_code}")
        return 'unknown'
    
    data = resp.json().get('data', {})
    attrs = data.get('attributes', {})
    
    # Check if there are any successful payments linked to this link
    payments = attrs.get('payments', [])
    if payments:
        # Check if any payment has status 'paid'
        for payment in payments:
            pay_attrs = payment.get('attributes', {})
            pay_status = pay_attrs.get('status', '')
            print(f"[DEBUG] Payment status for link {link_id}: {pay_status}")
            if pay_status == 'paid':
                return 'paid'
        # If payments exist but none are paid, check the first one
        if payments[0].get('attributes', {}).get('status') == 'failed':
            return 'failed'
    
    # If no payments yet, return link status
    link_status = attrs.get('status', 'unknown')
    print(f"[DEBUG] Link {link_id} status: {link_status}, payments: {len(payments)}")
    return link_status

def ensure_paymongo_table(db):
    """Ensure the paymongo_mock_links table exists (for mock payment mode)."""
    try:
        execute(db, """
            CREATE TABLE IF NOT EXISTS paymongo_mock_links (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                link_id       VARCHAR(50) NOT NULL UNIQUE,
                ref_code      VARCHAR(20) NOT NULL,
                amount        DECIMAL(10,2) NOT NULL DEFAULT 0.00,
                description   VARCHAR(255) NULL,
                status        ENUM('unpaid','paid','failed','expired') NOT NULL DEFAULT 'unpaid',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME NULL,
                INDEX idx_link_id (link_id),
                INDEX idx_ref_code (ref_code),
                INDEX idx_status (status)
            )
        """)
        db.commit()
        print("[OK] PayMongo mock links table initialized")
    except Exception as e:
        print(f"[WARNING] Error ensuring paymongo_mock_links table: {str(e)}")


RESERVATION_MINUTES = 45   # seat lock duration (increased for better UX)

def allowed_file(f):
    return '.' in f and f.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ─────────────────────────────────────────────────────────────
#  VALIDATORS
# ─────────────────────────────────────────────────────────────
def is_valid_email(v): return bool(re.match(r'^[\w\.-]+@[\w\.-]+\.\w{2,}$', v))
def is_valid_phone(v): return bool(re.match(r'^(\+63|0)\d{10}$', v))

# ─────────────────────────────────────────────────────────────
#  AUTH DECORATORS
# ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Admins can browse the main site freely
        if session.get('is_admin'):
            return f(*args, **kwargs)
        if 'user_id' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('login'))
        try:
            db = get_db()
            exists = query(db, "SELECT id FROM users WHERE id=%s",
                           (session['user_id'],), one=True)
            db.close()
            if not exists:
                session.clear()
                flash('Session expired. Please log in again.', 'warning')
                return redirect(url_for('login'))
        except Exception:
            pass
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────────────────────
#  DB / FORMATTING HELPERS
# ─────────────────────────────────────────────────────────────
def _fmt_time(t):
    if not t: return ''
    if isinstance(t, timedelta):
        total = int(t.total_seconds())
        hrs, rem = divmod(total, 3600)
        mins = rem // 60
    else:
        parts = str(t).split(':')
        hrs  = int(parts[0])
        mins = int(parts[1]) if len(parts) > 1 else 0
    suffix = 'AM' if hrs < 12 else 'PM'
    return f'{hrs % 12 or 12}:{mins:02d} {suffix}'

def run_maintenance(db):
    """Auto-complete past showings and release expired seat locks."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute(db, """
        UPDATE showings SET status='completed'
         WHERE status IN ('open','scheduled','full')
           AND CONCAT(show_date,' ',show_time) < %s
    """, (now,))
    execute(db, """
        UPDATE seats SET status='available', locked_until=NULL
         WHERE status='locked' AND locked_until < %s
    """, (now,))
    execute(db, """
        UPDATE bookings SET status='Completed'
         WHERE status='Confirmed'
           AND showing_id IN (SELECT id FROM showings WHERE status='completed')
    """)
    # Release expired pending-payment bookings (after 20 min)
    cutoff = (datetime.now() - timedelta(minutes=20)).strftime('%Y-%m-%d %H:%M:%S')
    execute(db, """
        UPDATE seats s
          JOIN bookings b ON b.seat_id=s.id
        SET s.status='available', s.locked_until=NULL
        WHERE b.payment_status='pending'
          AND b.created_at < %s
          AND s.status='locked'
    """, (cutoff,))
    execute(db, """
        UPDATE bookings SET status='Cancelled'
         WHERE payment_status='pending'
           AND created_at < %s
           AND status='Confirmed'
    """, (cutoff,))
    db.commit()

# ─────────────────────────────────────────────────────────────
#  SEAT SEEDING
# ─────────────────────────────────────────────────────────────
def seed_seats_default(db, showing_id):
    """Fallback: hardcoded 5-row layout if no hall config exists."""
    rows_config = [
        ('A', 'VIP'), ('B', 'VIP'),
        ('C', 'Standard'), ('D', 'Standard'), ('E', 'Standard'),
    ]
    for row_label, category in rows_config:
        for num in range(1, 11):
            execute(db, """
                INSERT IGNORE INTO seats
                    (showing_id, row_label, seat_number, seat_code, category, status)
                VALUES (%s,%s,%s,%s,%s,'available')
            """, (showing_id, row_label, num, f"{row_label}{num}", category))
    db.commit()

def seed_seats_from_hall(db, showing_id, hall_id):
    """Generate seats from a hall's admin-configured layout."""
    seat_configs = query(db, """
        SELECT * FROM hall_seat_config
        WHERE hall_id=%s AND is_active=1
        ORDER BY row_label, col_number
    """, (hall_id,))

    if not seat_configs:
        seed_seats_default(db, showing_id)
        return

    total = 0
    for sc in seat_configs:
        # Map seat_type to DB category enum (VIP or Standard; PWD maps to Standard)
        category = 'VIP' if sc['seat_type'] == 'VIP' else 'Standard'
        execute(db, """
            INSERT IGNORE INTO seats
                (showing_id, row_label, seat_number, seat_code, category, status)
            VALUES (%s,%s,%s,%s,%s,'available')
        """, (showing_id, sc['row_label'], sc['col_number'], sc['seat_code'], category))
        total += 1

    if total:
        execute(db, "UPDATE showings SET total_seats=%s WHERE id=%s", (total, showing_id))
    db.commit()

def ensure_seats(db, showing_id):
    """Seed seats for a showing if none exist yet."""
    row = query(db, "SELECT COUNT(*) AS cnt FROM seats WHERE showing_id=%s",
                (showing_id,), one=True)
    if row and row['cnt'] > 0:
        return

    showing = query(db, "SELECT cinema_id, hall_id FROM showings WHERE id=%s",
                    (showing_id,), one=True)
    if not showing:
        return

    hall_id = showing.get('hall_id')
    if not hall_id:
        # Auto-pick first configured hall for this cinema
        hall = query(db,
            "SELECT id FROM cinema_halls WHERE cinema_id=%s ORDER BY id LIMIT 1",
            (showing['cinema_id'],), one=True)
        if hall:
            hall_id = hall['id']

    if hall_id:
        seed_seats_from_hall(db, showing_id, hall_id)
    else:
        seed_seats_default(db, showing_id)

def ensure_future_showings(db, movie_id, cinema_id, days_ahead=3):
    """Only auto-creates showings if admin has NOT manually assigned any for this movie+cinema."""
    today = date.today().isoformat()
    limit = (date.today() + timedelta(days=days_ahead)).isoformat()

    # Check if admin has already assigned showings with explicit hall_id
    admin_assigned = query(db, """
        SELECT COUNT(*) AS cnt FROM showings
         WHERE movie_id=%s AND cinema_id=%s AND hall_id IS NOT NULL
           AND show_date >= %s
    """, (movie_id, cinema_id, today), one=True)

    # If admin has assigned showings, do NOT auto-generate — respect their setup
    if admin_assigned and admin_assigned.get('cnt', 0) > 0:
        return

    row = query(db, """
        SELECT COUNT(*) AS cnt FROM showings
         WHERE movie_id=%s AND cinema_id=%s
           AND show_date > %s AND show_date <= %s
           AND status IN ('open','scheduled')
    """, (movie_id, cinema_id, today, limit), one=True)

    if row and row.get('cnt', 0) < 2:
        # Find hall for this cinema (use first configured hall)
        hall = query(db,
            "SELECT id FROM cinema_halls WHERE cinema_id=%s ORDER BY id LIMIT 1",
            (cinema_id,), one=True)
        hall_id = hall['id'] if hall else None

        timeslots = ['10:00:00', '13:30:00', '16:30:00', '19:30:00', '22:00:00']
        for d in range(0, days_ahead + 1):
            show_date = (date.today() + timedelta(days=d)).isoformat()
            for t in timeslots:
                execute(db, """
                    INSERT IGNORE INTO showings
                        (movie_id, cinema_id, hall_id, show_date, show_time, status)
                    VALUES (%s,%s,%s,%s,%s,'open')
                """, (movie_id, cinema_id, hall_id, show_date, t))
        db.commit()

def get_movies_with_status(db):
    """Get all active movies with their status and next showing date."""
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = date.today().isoformat()
    raw = query(db, """
        SELECT m.id, m.title, m.genre, m.rating, m.poster_path, m.duration_mins, m.price,
               (SELECT MIN(s.show_date) FROM showings s
                 WHERE s.movie_id=m.id AND s.status IN ('open','scheduled')
                   AND CONCAT(s.show_date,' ',s.show_time) > %s
               ) AS next_date,
               (SELECT COUNT(*) FROM showings s
                 WHERE s.movie_id=m.id AND s.show_date=%s
                   AND s.status IN ('open','full')) AS today_count,
               (SELECT MAX(s.show_date) FROM showings s
                 WHERE s.movie_id=m.id AND s.status='completed') AS last_played
        FROM movies m WHERE m.status='active'
        ORDER BY today_count DESC, next_date ASC
    """, (now, today))
    today_dt = date.today()
    tomorrow = today_dt + timedelta(days=1)
    result = []
    for r in raw:
        row = dict(r)
        nd = row.get('next_date')
        tc = row.get('today_count') or 0
        # Build human-readable status tag
        if tc > 0:
            row['show_status'] = 'showing_today'
            row['show_label']  = 'Showing Today'
        elif nd:
            nd_dt = nd if isinstance(nd, date) else date.fromisoformat(str(nd))
            if nd_dt == tomorrow:
                row['show_status'] = 'showing_soon'
                row['show_label']  = f"Showing Tomorrow"
            else:
                fmt = nd_dt.strftime('%b %d')
                row['show_status'] = 'upcoming'
                row['show_label']  = f"Showing on {fmt}"
        else:
            row['show_status'] = 'ended'
            row['show_label']  = 'Run Ended'
        result.append(row)
    return result

# ─────────────────────────────────────────────────────────────
#  PUBLIC ROUTES
# ─────────────────────────────────────────────────────────────

# Initialize tables on startup
try:
    db = get_db()
    ensure_paymongo_table(db)
    db.close()
except Exception as e:
    print(f"[WARNING] Error initializing tables on startup: {str(e)}")


@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('landing.html')

@app.route('/home')
@login_required
def index():
    db = get_db()
    try:
        run_maintenance(db)
        movies = get_movies_with_status(db)
    finally:
        db.close()
    return render_template('index.html',
                           user_name=session.get('user_name') or session.get('admin_name', 'Admin'), movies=movies)

@app.route('/movies')
@login_required
def movies():
    db = get_db()
    try:
        run_maintenance(db)
        movies_list = get_movies_with_status(db)
    finally:
        db.close()
    return render_template('movies.html',
                           user_name=session.get('user_name') or session.get('admin_name', 'Admin'), movies=movies_list)

# ─────────────────────────────────────────────────────────────
#  DIAGNOSTIC ENDPOINT (FOR DEBUGGING SHOWINGS)
# ─────────────────────────────────────────────────────────────
@app.route('/debug/showings')
@login_required
def debug_showings():
    """Show all showings and their status (for debugging)."""
    if not session.get('is_admin'):
        return "Admin only", 403
    
    db = get_db()
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        showings = query(db, """
            SELECT s.id, m.title, h.hall_name, c.name as cinema,
                   s.show_date, s.show_time, s.status,
                   CONCAT(s.show_date, ' ', s.show_time) as datetime_combined,
                   (SELECT COUNT(*) FROM seats WHERE showing_id=s.id AND status='available') as avail_seats,
                   (SELECT COUNT(*) FROM seats WHERE showing_id=s.id) as total_seats
            FROM showings s
            JOIN movies m ON m.id=s.movie_id
            LEFT JOIN cinema_halls h ON h.id=s.hall_id
            LEFT JOIN cinemas c ON c.id=s.cinema_id
            ORDER BY s.show_date DESC, s.show_time DESC
            LIMIT 50
        """)
        
        html = f"""
        <html><head><title>DEBUG: Showings</title>
        <style>
            body {{ font-family: Arial; background: #1a1a1a; color: #fff; padding: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
            th, td {{ border: 1px solid #444; padding: 10px; text-align: left; }}
            th {{ background: #CE0000; }}
            tr:hover {{ background: #333; }}
            .ok {{ color: #4caf50; }}
            .warn {{ color: #ffb300; }}
            .error {{ color: #ff6666; }}
            h1 {{ color: #CE0000; }}
        </style>
        </head><body>
        <h1>🔍 Showings Diagnostic</h1>
        <p><strong>Current Time:</strong> {now}</p>
        <p><strong>Total Showings:</strong> {len(showings)}</p>
        
        <table>
        <tr>
            <th>ID</th>
            <th>Movie</th>
            <th>Hall</th>
            <th>Cinema</th>
            <th>Date & Time</th>
            <th>Status</th>
            <th>Available Seats</th>
            <th>Will Show in Booking?</th>
        </tr>
        """
        
        for s in showings:
            showing_datetime = f"{s['show_date']} {s['show_time']}"
            is_future = showing_datetime > now
            within_3_days = (date.fromisoformat(str(s['show_date'])) <= date.today() + timedelta(days=3))
            will_show = is_future and within_3_days and (s['avail_seats'] or 0) > 0
            
            status_class = "ok" if will_show else "warn" if is_future else "error"
            status_text = "✅ YES" if will_show else ("⏳ No (time passed)" if not is_future else "❌ No (>3 days)")
            
            html += f"""
            <tr>
                <td>{s['id']}</td>
                <td>{s['title']}</td>
                <td>{s['hall_name'] or '-'}</td>
                <td>{s['cinema'] or '-'}</td>
                <td>{showing_datetime}</td>
                <td>{s['status']}</td>
                <td>{s['avail_seats']} / {s['total_seats']}</td>
                <td class="{status_class}">{status_text}</td>
            </tr>
            """
        
        html += """
        </table>
        <p style="margin-top: 30px; padding: 15px; background: #222; border-left: 3px solid #CE0000;">
            <strong>Interpretation:</strong><br>
            • <span class="ok">Green ✅:</span> This showing WILL appear in booking<br>
            • <span class="warn">Yellow ⏳:</span> Time has passed, won't show<br>
            • <span class="error">Red ❌:</span> Won't show (check reason)<br>
            • Check "Date & Time" column to see exact datetime
        </p>
        </body></html>
        """
        
        return html
    finally:
        db.close()

@app.route('/debug/session')
def debug_session():
    """Debug endpoint to check session state"""
    return jsonify({
        'session_keys': list(session.keys()),
        'user_id': session.get('user_id'),
        'is_admin': session.get('is_admin'),
        'user_name': session.get('user_name'),
        'has_user_id': 'user_id' in session,
    })

# ───────────────────────────────────────────────────────────── FLOW
# ─────────────────────────────────────────────────────────────
@app.route('/booking')
@login_required
def booking():
    # Only regular users can access booking - redirect admins
    if session.get('is_admin'):
        flash('Admins use the admin dashboard. Regular users go to /booking.', 'info')
        return redirect(url_for('admin_dashboard'))
    
    # Ensure user has valid session
    if 'user_id' not in session or not session.get('user_id'):
        flash('Session invalid. Please log in again.', 'warning')
        return redirect(url_for('login'))
    
    db = get_db()
    try:
        run_maintenance(db)

        movie_id   = request.args.get('movie_id',   type=int)
        showing_id = request.args.get('showing_id', type=int)

        today = date.today().isoformat()
        limit = (date.today() + timedelta(days=3)).isoformat()
        now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        all_movies = get_movies_with_status(db)

        selected_movie   = None
        showings_by_date = {}
        selected_showing = None
        seat_rows        = []
        vip_rows         = []
        std_rows         = []
        active_count     = 0

        if movie_id:
            selected_movie = query(db,
                "SELECT * FROM movies WHERE id=%s AND status='active'",
                (movie_id,), one=True)

            if selected_movie:
                raw_showings = query(db, """
                    SELECT s.id, s.show_date, s.show_time, s.status, s.total_seats,
                           s.hall_id,
                           c.name AS cinema_name, c.location AS cinema_location,
                           h.hall_name,
                           COALESCE(
                               (SELECT COUNT(*) FROM seats st
                                WHERE st.showing_id=s.id AND st.status='booked'),0
                           ) AS booked_count,
                           COALESCE(
                               (SELECT COUNT(*) FROM seats st
                                WHERE st.showing_id=s.id AND st.status='available'),0
                           ) AS avail_count,
                           COALESCE(
                               (SELECT COUNT(*) FROM seats st
                                WHERE st.showing_id=s.id),0
                           ) AS total_seeded
                    FROM showings s
                    JOIN cinemas c ON c.id=s.cinema_id
                    LEFT JOIN cinema_halls h ON h.id=s.hall_id
                    WHERE s.movie_id=%s
                      AND s.status IN ('open','scheduled','full')
                      AND CONCAT(s.show_date,' ',s.show_time) > %s
                      AND s.show_date <= %s
                    ORDER BY s.show_date, s.show_time
                """, (movie_id, now, limit))

                for sh in raw_showings:
                    sh = dict(sh)
                    if sh['total_seeded'] == 0:
                        ensure_seats(db, sh['id'])
                        sh['avail_count'] = 50
                    if sh['avail_count'] == 0 and sh['booked_count'] == 0:
                        sh['avail_count'] = sh['total_seats']

                    d_obj   = sh['show_date']
                    d_str   = d_obj.isoformat() if hasattr(d_obj, 'isoformat') else str(d_obj)
                    d_label = d_obj.strftime('%A, %B %d %Y') if hasattr(d_obj, 'strftime') else d_str

                    if d_str not in showings_by_date:
                        showings_by_date[d_str] = {'label': d_label, 'showings': []}

                    avail = sh['avail_count']
                    if avail == 0:
                        sh['avail_label'] = 'SOLD OUT'
                        sh['avail_class'] = 'full'
                    elif avail <= 8:
                        sh['avail_label'] = f'Only {avail} left!'
                        sh['avail_class'] = 'low'
                    else:
                        sh['avail_label'] = f'{avail} of {sh["total_seats"]} available'
                        sh['avail_class'] = 'ok'

                    sh['show_time_fmt'] = _fmt_time(sh['show_time'])
                    showings_by_date[d_str]['showings'].append(sh)

        if showing_id:
            ensure_seats(db, showing_id)
            row = query(db, """
                SELECT s.id, s.show_date, s.show_time, s.status AS show_status,
                       s.total_seats, s.hall_id,
                       c.name AS cinema_name, c.location AS cinema_location,
                       h.hall_name,
                       m.title AS movie_title, m.genre, m.rating, m.poster_path, m.price,
                       m.id AS movie_id_val
                FROM showings s
                JOIN cinemas c ON c.id=s.cinema_id
                LEFT JOIN cinema_halls h ON h.id=s.hall_id
                JOIN movies  m ON m.id=s.movie_id
                WHERE s.id=%s
            """, (showing_id,), one=True)

            if row:
                selected_showing = dict(row)
                selected_showing['show_time_fmt'] = _fmt_time(selected_showing['show_time'])
                d_obj = selected_showing['show_date']
                selected_showing['show_date_fmt'] = (
                    d_obj.strftime('%A, %B %d %Y') if hasattr(d_obj, 'strftime') else str(d_obj))
                if not movie_id:
                    movie_id = selected_showing['movie_id_val']
                if not selected_movie:
                    selected_movie = {
                        'id':          selected_showing['movie_id_val'],
                        'title':       selected_showing['movie_title'],
                        'genre':       selected_showing['genre'],
                        'rating':      selected_showing['rating'],
                        'poster_path': selected_showing['poster_path'],
                    }

            all_seats_raw = query(db, """
                SELECT st.id, st.row_label, st.seat_number, st.seat_code,
                       st.category, st.status, st.locked_until
                FROM seats st
                WHERE st.showing_id=%s
                ORDER BY st.row_label, st.seat_number
            """, (showing_id,))

            # Build full grid (with aisles) from hall_seat_config
            hall_id_for_grid = selected_showing.get('hall_id') if selected_showing else None

            if hall_id_for_grid:
                hall_config = query(db, """
                    SELECT row_label, col_number, seat_type, is_active, seat_code
                    FROM hall_seat_config WHERE hall_id=%s
                    ORDER BY row_label, col_number
                """, (hall_id_for_grid,))

                seat_map_by_code = {s['seat_code']: dict(s) for s in all_seats_raw} if all_seats_raw else {}
                hall_grid = {}
                for hc in hall_config:
                    rl = hc['row_label']
                    if rl not in hall_grid:
                        hall_grid[rl] = {}
                    hall_grid[rl][hc['col_number']] = dict(hc)

                seat_rows = []
                for rl in sorted(hall_grid.keys()):
                    row_seats = []
                    for cn in sorted(hall_grid[rl].keys()):
                        cfg = hall_grid[rl][cn]
                        if not cfg['is_active']:
                            row_seats.append({
                                'id': None, 'row_label': rl, 'seat_number': cn,
                                'seat_code': cfg['seat_code'], 'category': 'Aisle',
                                'status': 'aisle', 'is_aisle': True
                            })
                        else:
                            seat = seat_map_by_code.get(cfg['seat_code'], {})
                            cat = cfg['seat_type']  # Regular/VIP/PWD
                            row_seats.append({
                                'id': seat.get('id'), 'row_label': rl, 'seat_number': cn,
                                'seat_code': cfg['seat_code'], 'category': cat,
                                'status': seat.get('status', 'available'), 'is_aisle': False
                            })
                            active_count += 1
                            if cat == 'VIP' and rl not in vip_rows:
                                vip_rows.append(rl)
                            elif cat != 'VIP' and rl not in std_rows:
                                std_rows.append(rl)
                    seat_rows.append({'label': rl, 'seats': row_seats, 'category': 'Mixed'})
            else:
                # Fallback: use seats table directly (no aisle info)
                from collections import defaultdict
                rows_dict = defaultdict(list)
                for s in all_seats_raw:
                    rows_dict[s['row_label']].append(dict(s) | {'is_aisle': False})
                    active_count += 1
                    if s['category'] == 'VIP' and s['row_label'] not in vip_rows:
                        vip_rows.append(s['row_label'])
                    elif s['category'] != 'VIP' and s['row_label'] not in std_rows:
                        std_rows.append(s['row_label'])
                seat_rows = [{'label': k, 'seats': v, 'category': v[0]['category']}
                             for k, v in sorted(rows_dict.items())]

    finally:
        db.close()

    return render_template('booking.html',
        user_name        = session.get('user_name'),
        all_movies       = all_movies,
        selected_movie   = selected_movie,
        movie_id         = movie_id,
        movie_price      = selected_showing.get('price', 450) if selected_showing else 450,
        showings_by_date = showings_by_date,
        selected_showing = selected_showing,
        showing_id       = showing_id,
        seat_rows        = seat_rows,
        vip_rows         = vip_rows,
        std_rows         = std_rows,
        active_seat_count= active_count,
        booking_success  = False,
        errors={}, form={},
        ticket_prices    = TICKET_PRICES,
    )

# ─────────────────────────────────────────────────────────────
#  SEAT API  (real-time lock / unlock / status)
# ─────────────────────────────────────────────────────────────
@app.route('/api/lock-seat', methods=['POST'])
@login_required
def lock_seat():
    data       = request.get_json(force=True)
    seat_id    = data.get('seat_id')
    showing_id = data.get('showing_id')
    if not seat_id or not showing_id:
        return jsonify({'ok': False, 'msg': 'Missing params'})

    db  = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        # Release expired locks first
        execute(db, """
            UPDATE seats SET status='available', locked_until=NULL
             WHERE showing_id=%s AND status='locked' AND locked_until < %s
        """, (showing_id, now))

        seat = query(db, "SELECT status FROM seats WHERE id=%s", (seat_id,), one=True)
        if not seat or seat['status'] != 'available':
            db.close()
            return jsonify({'ok': False, 'msg': 'Seat no longer available'})

        lock_exp = (datetime.now() + timedelta(minutes=RESERVATION_MINUTES)
                    ).strftime('%Y-%m-%d %H:%M:%S')
        execute(db, "UPDATE seats SET status='locked', locked_until=%s WHERE id=%s",
                (lock_exp, seat_id))
        db.commit()
        return jsonify({'ok': True, 'expires': lock_exp})
    except Exception as e:
        try:
            db.rollback()
        except:
            pass
        return jsonify({'ok': False, 'msg': str(e)})
    finally:
        db.close()

@app.route('/api/unlock-seat', methods=['POST'])
@login_required
def unlock_seat():
    data    = request.get_json(force=True)
    seat_id = data.get('seat_id')
    if not seat_id:
        return jsonify({'ok': False})
    db = get_db()
    try:
        execute(db,
            "UPDATE seats SET status='available', locked_until=NULL WHERE id=%s AND status='locked'",
            (seat_id,))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        try:
            db.rollback()
        except:
            pass
        return jsonify({'ok': False, 'msg': str(e)})
    finally:
        db.close()

@app.route('/api/seat-status/<int:showing_id>')
@login_required
def seat_status(showing_id):
    db  = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        execute(db, """
            UPDATE seats SET status='available', locked_until=NULL
             WHERE showing_id=%s AND status='locked' AND locked_until < %s
        """, (showing_id, now))
        db.commit()
        seats = query(db, """
            SELECT id, seat_code, status, category, row_label, seat_number
            FROM seats WHERE showing_id=%s ORDER BY row_label, seat_number
        """, (showing_id,))
        return jsonify({'seats': seats})
    except Exception as e:
        try:
            db.rollback()
        except:
            pass
        return jsonify({'error': str(e)})
    finally:
        db.close()

# ─────────────────────────────────────────────────────────────
#  CONFIRM BOOKING  →  redirect to Fake Payment Page
# ─────────────────────────────────────────────────────────────
@app.route('/booking/confirm', methods=['POST'])
@login_required
def confirm_booking():
    # Admins cannot book - only users can
    if session.get('is_admin'):
        flash('Admins cannot make bookings.', 'error')
        return redirect(url_for('admin_dashboard'))
    
    # Ensure user_id exists in session and store it
    if 'user_id' not in session:
        flash('Please log in to make a booking.', 'warning')
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')  # Store it safely
    if not user_id:
        flash('Session invalid. Please log in again.', 'warning')
        return redirect(url_for('login'))
    
    seat_ids_raw  = request.form.get('seat_ids', '').strip()
    showing_id    = request.form.get('showing_id', type=int)
    ticket_type   = request.form.get('ticket_type', 'Regular')
    customer_name = request.form.get('customer_name', '').strip()
    contact       = request.form.get('contact', '').strip()
    special       = request.form.get('special_requests', '').strip()
    payment_mode  = request.form.get('payment_mode', 'walkin')   # 'walkin' or 'online'

    errors = {}
    if not seat_ids_raw:
        errors['seats'] = 'Please select at least one seat.'
    if not showing_id:
        errors['showing'] = 'Invalid showing.'
    if not customer_name or len(customer_name) < 2:
        errors['customer_name'] = 'Valid name required (min 2 chars).'
    if not contact or not re.match(r'^(\+63|0)\d{10}$', contact):
        errors['contact'] = 'Enter a valid PH mobile (09XXXXXXXXX).'
    if ticket_type != 'Regular':
        errors['ticket_type'] = 'Invalid ticket type.'

    seat_ids = [int(x) for x in seat_ids_raw.split(',') if x.strip().isdigit()]
    if not seat_ids:
        errors['seats'] = 'No valid seats selected.'
    elif len(seat_ids) > 10:
        errors['seats'] = 'Maximum 10 seats per booking.'

    if errors:
        flash(' | '.join(errors.values()), 'error')
        return redirect(url_for('booking', showing_id=showing_id))

    db = get_db()
    try:
        showing = query(db, "SELECT id FROM showings WHERE id=%s AND status IN ('open', 'scheduled', 'full')", (showing_id,), one=True)
        if not showing:
            flash('This showing is no longer available.', 'error')
            db.close()
            return redirect(url_for('booking'))

        # Verify seats are still available or locked by this user
        for sid in seat_ids:
            seat = query(db, "SELECT seat_code, status FROM seats WHERE id=%s", (sid,), one=True)
            if not seat or seat['status'] == 'booked':
                code = seat['seat_code'] if seat else str(sid)
                flash(f'Seat {code} was just taken. Please re-select.', 'error')
                db.close()
                return redirect(url_for('booking', showing_id=showing_id))

        # ── GET SEAT DETAILS WITH CATEGORIES (from hall layout) ──────────
        seat_info_list = []
        if seat_ids:
            placeholders = ','.join(['%s'] * len(seat_ids))
            seat_info_list = query(db,
                f"SELECT id, seat_code, category FROM seats WHERE id IN ({placeholders})", seat_ids)

        # ── GET MOVIE PRICE FROM DATABASE ─────────────────
        movie = query(db, "SELECT price FROM movies WHERE id=%s", (showing['id'],), one=True)
        movie_price = movie['price'] if movie else 450  # Default to 450 if not found

        # ── CALCULATE TOTAL USING MOVIE PRICE + HALL SEAT CATEGORIES ─────────
        # Seat category multipliers: VIP = 650/450 = 1.44x; Standard/Regular = 1.0x
        CATEGORY_MULTIPLIERS = {'VIP': 650/450, 'Standard': 1.0, 'Regular': 1.0, 'PWD': 1.0}
        total_price = 0
        for si in seat_info_list:
            category = si['category']
            multiplier = CATEGORY_MULTIPLIERS.get(category, 1.0)
            seat_price = round(movie_price * multiplier)
            total_price += seat_price
        unit_price = round(total_price / len(seat_ids)) if seat_ids else movie_price
        ref_code    = 'TKT-' + uuid.uuid4().hex[:8].upper()

        # ✅ No discounts - all tickets are regular price
        discount_status = 'none'

        seat_codes_str = ', '.join(f"{s['seat_code']} ({s['category']})" for s in seat_info_list)

        sh_info = query(db, """
            SELECT m.title, c.name AS cinema, s.show_date, s.show_time
            FROM showings s
            JOIN movies  m ON m.id=s.movie_id
            JOIN cinemas c ON c.id=s.cinema_id
            WHERE s.id=%s
        """, (showing_id,), one=True)

        # ── Determine payment status based on payment mode ─────────
        # Walk-in bookings get 'walkin_pending' status
        # Online bookings get 'pending' status
        payment_status = 'walkin_pending' if payment_mode == 'walkin' else 'pending'

        # ── Lock seats + create booking records with correct payment_status ─────────
        lock_exp = (datetime.now() + timedelta(minutes=RESERVATION_MINUTES)
                    ).strftime('%Y-%m-%d %H:%M:%S')

        for sid in seat_ids:
            execute(db, "UPDATE seats SET status='locked', locked_until=%s WHERE id=%s",
                    (lock_exp, sid))
            execute(db, """
                INSERT INTO bookings
                    (user_id, showing_id, seat_id, booking_ref, ref_code,
                     ticket_type, ticket_count, unit_price, total_price,
                     seat_codes, customer_name, contact, special_requests,
                     discount_status, payment_status, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (user_id, showing_id, sid, ref_code, ref_code,
                  ticket_type, len(seat_ids), unit_price, total_price,
                  seat_codes_str, customer_name, contact, special, discount_status,
                  payment_status, 'Confirmed'))

        db.commit()

        # Walk-in: mark seats as booked immediately + redirect to ticket
        if payment_mode == 'walkin':
            # ✅ Seats are booked immediately for walk-in (no payment page)
            for sid in seat_ids:
                execute(db, "UPDATE seats SET status='booked', locked_until=NULL WHERE id=%s", (sid,))
            db.commit()
            return redirect(url_for('booking_ticket', ref=ref_code))

        # Online payment: redirect to payment page
        return redirect(url_for('payment_checkout', ref=ref_code))

    except Exception as e:
        try:
            db.rollback()
        except:
            pass
        error_msg = str(e)
        error_type = type(e).__name__
        
        # Debug logging with full context
        session_info = {
            'has_user_id': 'user_id' in session,
            'is_admin': session.get('is_admin'),
            'user_id_value': session.get('user_id'),
        }
        print(f"❌ Booking error for user {user_id}: [{error_type}] {error_msg}")
        print(f"   Session state: {session_info}")
        
        # Handle session errors specifically
        if 'user_id' in error_msg or 'KeyError' in error_msg or 'user_id' in str(e.__class__):
            flash('Session error. Please log in again and try booking.', 'error')
            session.clear()
            return redirect(url_for('login'))
        
        # Generic booking error
        flash(f'Booking failed: {error_msg[:100]}', 'error')
        return redirect(url_for('booking', showing_id=showing_id))
    finally:
        db.close()

@app.route('/booking/ticket')
@login_required
def booking_ticket():
    """Walk-in ticket confirmation page — payment at cinema counter."""
    ref_code = request.args.get('ref', '').strip()
    if not ref_code:
        flash('Invalid booking reference.', 'error')
        return redirect(url_for('booking'))

    db = get_db()
    try:
        booking = query(db, """
            SELECT b.ref_code, b.customer_name, b.ticket_type,
                   b.total_price, b.ticket_count, b.seat_codes,
                   b.payment_status, b.discount_status, b.status,
                   m.title AS movie, m.poster_path,
                   c.name AS cinema,
                   h.hall_name,
                   s.show_date, s.show_time
            FROM bookings b
            JOIN showings s ON s.id=b.showing_id
            JOIN movies   m ON m.id=s.movie_id
            JOIN cinemas  c ON c.id=s.cinema_id
            LEFT JOIN cinema_halls h ON h.id=s.hall_id
            WHERE b.ref_code=%s AND b.user_id=%s
            LIMIT 1
        """, (ref_code, session['user_id']), one=True)
    finally:
        db.close()

    if not booking:
        flash('Booking not found.', 'error')
        return redirect(url_for('booking'))

    booking = dict(booking)
    d_obj = booking['show_date']
    booking['date_fmt'] = d_obj.strftime('%A, %B %d %Y') if hasattr(d_obj, 'strftime') else str(d_obj)
    booking['time_fmt'] = _fmt_time(booking['show_time'])

    return render_template('booking_ticket.html',
                           user_name=session.get('user_name') or session.get('admin_name', 'Admin'),
                           booking=booking)


@app.route('/payment/checkout')
@login_required
def payment_checkout():
    ref_code = request.args.get('ref', '').strip()
    if not ref_code:
        flash('Invalid booking reference.', 'error')
        return redirect(url_for('booking'))

    db = get_db()
    try:
        booking = query(db, """
            SELECT b.ref_code, b.customer_name, b.ticket_type,
                   b.total_price, b.ticket_count, b.seat_codes,
                   b.payment_status, b.discount_status, b.created_at,
                   m.title AS movie, m.poster_path,
                   c.name AS cinema,
                   s.show_date, s.show_time
            FROM bookings b
            JOIN showings s ON s.id=b.showing_id
            JOIN movies   m ON m.id=s.movie_id
            JOIN cinemas  c ON c.id=s.cinema_id
            WHERE b.ref_code=%s AND b.user_id=%s
            LIMIT 1
        """, (ref_code, session['user_id']), one=True)
    except Exception as e:
        print(f"❌ Error loading booking {ref_code}: {str(e)}")
        flash(f'Error loading booking: {str(e)}', 'error')
        db.close()
        return redirect(url_for('booking'))
    finally:
        db.close()

    if not booking:
        print(f"⚠️ Booking not found: {ref_code} for user {session['user_id']}")
        flash('Booking not found or already processed.', 'error')
        return redirect(url_for('booking'))

    # ✅ CRITICAL VALIDATION: Reject walk-in bookings (only online payments here)
    if booking['payment_status'] == 'walkin_pending':
        print(f"⚠️ Walk-in booking {ref_code} attempted to access payment page. Redirecting to receipt.")
        flash('This is a walk-in booking. Payment is collected at the cinema counter.', 'info')
        return redirect(url_for('booking_ticket', ref=ref_code))

    # If already paid, go straight to result
    if booking['payment_status'] == 'paid':
        return redirect(url_for('payment_result', ref=ref_code))

    booking = dict(booking)
    d_obj = booking['show_date']
    booking['date_fmt'] = d_obj.strftime('%a, %b %d %Y') if hasattr(d_obj, 'strftime') else str(d_obj)
    booking['time_fmt'] = _fmt_time(booking['show_time'])

    # Calculate how many minutes left on reservation
    created = booking['created_at']
    elapsed = (datetime.now() - created).total_seconds() if created else 0
    secs_left = max(0, int(RESERVATION_MINUTES * 60 - elapsed))

    # Show PayMongo option if keys are configured (real mode) OR if mock mode is enabled
    paymongo_available = bool(PAYMONGO_SECRET_KEY) and REQUESTS_AVAILABLE

    return render_template('payment_page.html',
                           user_name=session.get('user_name') or session.get('admin_name', 'Admin'),
                           booking=booking,
                           secs_left=secs_left,
                           use_paymongo=paymongo_available,
                           paymongo_public_key=PAYMONGO_PUBLIC_KEY,
)

@app.route('/payment/process', methods=['POST'])
@login_required
def payment_process():
    """
    Process payment via PayMongo (test mode) if keys are configured,
    otherwise fall back to simulation.
    """
    ref_code = request.form.get('ref_code', '').strip()
    method   = request.form.get('payment_method', 'card')

    if not ref_code:
        return jsonify({'ok': False, 'msg': 'Invalid booking reference.', 'status': 'error', 'payment_id': None})

    # Walk-in should NOT reach this endpoint - it's only for ONLINE payments
    if method == 'walk_in' or method == 'walkin':
        return jsonify({'ok': False, 'msg': 'Walk-in bookings must be completed during checkout, not here.', 'status': 'error', 'payment_id': None})

    # ✅ Validate payment method
    VALID_METHODS = ['gcash', 'maya', 'credit_card', 'debit_card', 'paymongo']
    if method not in VALID_METHODS:
        return jsonify({'ok': False, 'msg': 'Invalid payment method. Valid methods: GCash, Maya, Credit Card, Debit Card, PayMongo', 'status': 'error', 'payment_id': None})

    if not session.get('user_id'):
        return jsonify({'ok': False, 'msg': 'Not authenticated. Please log in.', 'status': 'error', 'payment_id': None})

    db = get_db()
    try:
        # Get all bookings with this ref_code
        bookings_list = query(db, """
            SELECT b.id, b.seat_id, b.showing_id, b.total_price, b.payment_status,
                   m.title AS movie
            FROM bookings b
            JOIN showings s ON s.id=b.showing_id
            JOIN movies   m ON m.id=s.movie_id
            WHERE b.ref_code=%s AND b.user_id=%s
        """, (ref_code, session['user_id']))

        if not bookings_list:
            db.close()
            return jsonify({'ok': False, 'msg': f'Booking {ref_code} not found.', 'status': 'error', 'payment_id': None})

        if bookings_list[0]['payment_status'] == 'paid':
            db.close()
            return jsonify({'ok': True, 'status': 'paid', 'msg': 'Payment already processed!', 'payment_id': None})

        amount     = float(bookings_list[0]['total_price'])
        movie_title = bookings_list[0].get('movie', 'Movie Ticket')

        # ── PAYMONGO MODE (Create link and auto-confirm for testing) ──────────────────────────────
        if method == 'paymongo':
            try:
                amount_centavos = int(amount * 100)
                success_url = url_for('paymongo_callback', ref=ref_code, _external=True)
                
                # Get the specific PayMongo method preference (card, gcash, maya, qr)
                paymongo_method = request.form.get('paymongo_method', 'card').strip()
                method_names = {
                    'card': 'Card',
                    'gcash': 'GCash',
                    'maya': 'Maya',
                    'qr': 'QR Code'
                }
                display_method = method_names.get(paymongo_method, 'Card')
                
                description = f'TICK.IT — {movie_title} ({ref_code}) via {display_method}'
                link_id, checkout_url = create_paymongo_link(
                    amount_centavos, description, ref_code, success_url)

                # ✅ AUTO-CONFIRM FOR TESTING: Create link in PayMongo but mark payment as paid immediately
                # This way: payment link shows in PayMongo Dashboard AND booking is instantly confirmed
                for b in bookings_list:
                    execute(db, "UPDATE seats SET status='booked', locked_until=NULL WHERE id=%s",
                            (b['seat_id'],))
                
                execute(db, """
                    UPDATE bookings SET payment_status='paid', status='Confirmed' WHERE ref_code=%s
                """, (ref_code,))
                
                execute(db, """
                    INSERT INTO payments
                        (booking_ref, user_id, amount, payment_method, paymongo_link_id, status, paid_at)
                    VALUES (%s,%s,%s,%s,%s,'paid',NOW())
                    ON DUPLICATE KEY UPDATE paymongo_link_id=%s, status='paid', paid_at=NOW()
                """, (ref_code, session['user_id'], amount, method, link_id, link_id))
                db.commit()
                
                # Check if all seats are booked to mark showing as full
                if bookings_list:
                    avail = query(db,
                        "SELECT COUNT(*) AS cnt FROM seats WHERE showing_id=%s AND status='available'",
                        (bookings_list[0]['showing_id'],), one=True)
                    if avail and avail['cnt'] == 0:
                        execute(db, "UPDATE showings SET status='full' WHERE id=%s",
                                (bookings_list[0]['showing_id'],))
                        db.commit()
                
                db.close()
                print(f"[OK] PayMongo link created: {link_id} ({display_method}) | Payment auto-confirmed for {ref_code}")
                return jsonify({
                    'ok':          True,
                    'status':      'paid',
                    'payment_id':   link_id,
                    'msg':         f'✅ Payment confirmed! Booked via {display_method}.'
                })
            except Exception as e:
                db.rollback()
                print(f"[ERROR] PayMongo error for {ref_code}: {str(e)}")  # Debug logging
                return jsonify({'ok': False, 'msg': f'PayMongo error: {str(e)}', 'status': 'error', 'payment_id': None})

        # ── SIMULATION FALLBACK (when no real keys configured) ───────────────
        roll = random.random()
        if roll < PAY_SUCCESS_RATE:
            pay_status = 'paid'
        elif roll < PAY_SUCCESS_RATE + PAY_FAILED_RATE:
            pay_status = 'failed'
        else:
            pay_status = 'pending'

        fake_id = 'SIM-' + uuid.uuid4().hex[:14].upper()

        if pay_status == 'paid':
            for b in bookings_list:
                execute(db, "UPDATE seats SET status='booked', locked_until=NULL WHERE id=%s",
                        (b['seat_id'],))
            execute(db,
                "UPDATE bookings SET payment_status='paid', status='Confirmed' WHERE ref_code=%s",
                (ref_code,))
            execute(db, """
                INSERT INTO payments
                    (booking_ref, user_id, amount, payment_method, paymongo_link_id, status, paid_at)
                VALUES (%s,%s,%s,%s,%s,'paid',NOW())
            """, (ref_code, session['user_id'], amount, method, fake_id))
            avail = query(db,
                "SELECT COUNT(*) AS cnt FROM seats WHERE showing_id=%s AND status='available'",
                (bookings_list[0]['showing_id'],), one=True)
            if avail and avail['cnt'] == 0:
                execute(db, "UPDATE showings SET status='full' WHERE id=%s",
                        (bookings_list[0]['showing_id'],))

        elif pay_status == 'failed':
            for b in bookings_list:
                execute(db,
                    "UPDATE seats SET status='available', locked_until=NULL WHERE id=%s",
                    (b['seat_id'],))
            execute(db,
                "UPDATE bookings SET payment_status='failed', status='Cancelled' WHERE ref_code=%s",
                (ref_code,))
            execute(db, """
                INSERT INTO payments
                    (booking_ref, user_id, amount, payment_method, paymongo_link_id, status, failed_at)
                VALUES (%s,%s,%s,%s,%s,'failed',NOW())
            """, (ref_code, session['user_id'], amount, method, fake_id))
        else:
            execute(db, """
                INSERT INTO payments
                    (booking_ref, user_id, amount, payment_method, paymongo_link_id, status)
                VALUES (%s,%s,%s,%s,%s,'pending')
            """, (ref_code, session['user_id'], amount, method, fake_id))

        db.commit()
        msgs = {
            'paid':    '✅ Payment successful! Your booking is confirmed. 🎉',
            'failed':  '❌ Payment declined. Your seats have been released.',
            'pending': '⏳ Payment is processing. We\'ll confirm shortly.',
        }
        print(f"[OK] Payment {ref_code} processed: {pay_status}")  # Debug logging
        return jsonify({
            'ok':        pay_status != 'error',
            'status':    pay_status,
            'payment_id': fake_id,
            'msg':       msgs.get(pay_status, 'Unknown payment status'),
        })

    except Exception as e:
        try:
            db.rollback()
        except:
            pass
        print(f"[ERROR] Payment error for {ref_code}: {str(e)}")  # Debug logging
        return jsonify({'ok': False, 'msg': f'Payment error: {str(e)}', 'status': 'error', 'payment_id': None})
    finally:
        db.close()

@app.route('/payment/result')
@login_required
def payment_result():
    # Redirect admins - they don't have personal payment results
    if session.get('is_admin'):
        flash('Admins cannot view user payment results. Use admin dashboard instead.', 'warning')
        return redirect(url_for('admin_dashboard'))
    
    # Ensure user_id exists in session
    if 'user_id' not in session:
        flash('Please log in to view payment results.', 'warning')
        return redirect(url_for('login'))
    
    ref_code = request.args.get('ref', '').strip()
    db = get_db()
    booking_data = None
    pay_row = None

    try:
        if ref_code:
            # Auto-verify PayMongo payment if not yet resolved
            if USE_PAYMONGO:
                pr = query(db,
                    "SELECT paymongo_link_id, status FROM payments WHERE booking_ref=%s ORDER BY id DESC LIMIT 1",
                    (ref_code,), one=True)
                if pr and pr.get('paymongo_link_id') and pr.get('status') == 'pending':
                    try:
                        link_status = verify_paymongo_link(pr['paymongo_link_id'])
                        if link_status == 'paid':
                            bl = query(db, "SELECT id, seat_id, showing_id FROM bookings WHERE ref_code=%s", (ref_code,))
                            for b in bl:
                                execute(db, "UPDATE seats SET status='booked', locked_until=NULL WHERE id=%s", (b['seat_id'],))
                            execute(db, "UPDATE bookings SET payment_status='paid', status='Confirmed' WHERE ref_code=%s", (ref_code,))
                            execute(db, "UPDATE payments SET status='paid', paid_at=NOW() WHERE booking_ref=%s", (ref_code,))
                            if bl:
                                av = query(db, "SELECT COUNT(*) AS cnt FROM seats WHERE showing_id=%s AND status='available'", (bl[0]['showing_id'],), one=True)
                                if av and av['cnt'] == 0:
                                    execute(db, "UPDATE showings SET status='full' WHERE id=%s", (bl[0]['showing_id'],))
                            db.commit()
                    except Exception:
                        pass

            row = query(db, """
                SELECT b.ref_code, b.ticket_type, b.total_price, b.ticket_count,
                       b.seat_codes, b.customer_name, b.status, b.payment_status,
                       b.discount_status,
                       m.title AS movie, m.poster_path, c.name AS cinema,
                       h.hall_name,
                       s.show_date, s.show_time
                FROM bookings b
                JOIN showings s ON s.id=b.showing_id
                JOIN movies   m ON m.id=s.movie_id
                JOIN cinemas  c ON c.id=s.cinema_id
                LEFT JOIN cinema_halls h ON h.id=s.hall_id
                WHERE b.ref_code=%s AND b.user_id=%s
                LIMIT 1
            """, (ref_code, session['user_id']), one=True)

            pay_row = query(db,
                "SELECT * FROM payments WHERE booking_ref=%s ORDER BY id DESC LIMIT 1",
                (ref_code,), one=True)

            if row:
                booking_data = dict(row)
                d_obj = booking_data['show_date']
                booking_data['date_fmt'] = (
                    d_obj.strftime('%A, %B %d %Y') if hasattr(d_obj, 'strftime') else str(d_obj))
                booking_data['time_fmt'] = _fmt_time(booking_data['show_time'])
    finally:
        db.close()

    payment_status = booking_data['payment_status'] if booking_data else 'unknown'
    return render_template('payment_success.html',
                           user_name=session.get('user_name') or session.get('admin_name', 'Admin'),
                           booking=booking_data,
                           payment_status=payment_status,
                           pay_row=pay_row)

# Keep old /payment/success route as alias for backward compat
@app.route('/payment/success')
@login_required
def payment_success():
    ref = request.args.get('ref', '')
    return redirect(url_for('payment_result', ref=ref))

@app.route('/payment/cancel')
@login_required
def payment_cancel():
    ref_code = request.args.get('ref', '')
    if ref_code:
        try:
            db = get_db()
            seats_to_free = query(db,
                "SELECT seat_id FROM bookings WHERE ref_code=%s", (ref_code,))
            for s in seats_to_free:
                execute(db,
                    "UPDATE seats SET status='available', locked_until=NULL WHERE id=%s",
                    (s['seat_id'],))
            execute(db,
                "UPDATE bookings SET payment_status='failed', status='Cancelled' WHERE ref_code=%s",
                (ref_code,))
            execute(db,
                "INSERT INTO payments (booking_ref, status) VALUES (%s, 'failed')",
                (ref_code,))
            db.commit()
        except Exception:
            pass
        finally:
            try:
                db.close()
            except:
                pass
    flash('Payment cancelled. Your seats have been released.', 'warning')
    return redirect(url_for('booking'))

@app.route('/payment/mock-checkout')
def paymongo_mock_checkout():
    """
    Mock PayMongo checkout page (for testing without real PayMongo keys).
    User can choose to complete payment or simulate failure.
    """
    link_id = request.args.get('link_id', '').strip()
    ref = request.args.get('ref', '').strip()
    amount = request.args.get('amount', '0').strip()
    
    if not link_id or not ref:
        return "Invalid mock checkout link", 400
    
    try:
        amount_pesos = float(amount) / 100
    except:
        amount_pesos = 0
    
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mock PayMongo Checkout</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
                 background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                 display: flex; align-items: center; justify-content: center; 
                 min-height: 100vh; margin: 0; padding: 20px; }}
        .container {{ background: white; border-radius: 12px; max-width: 420px; width: 100%; 
                      padding: 40px; box-shadow: 0 10px 40px rgba(0,0,0,0.3); }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .logo {{ font-size: 24px; font-weight: 900; color: #667eea; margin-bottom: 10px; }}
        .title {{ font-size: 22px; font-weight: 700; color: #333; margin: 0; }}
        .divider {{ height: 2px; background: #667eea; width: 60px; margin: 16px auto; }}
        
        .payment-summary {{ background: #f8f9fa; border-radius: 8px; padding: 20px; margin-bottom: 30px; }}
        .summary-row {{ display: flex; justify-content: space-between; margin-bottom: 12px; font-size: 14px; }}
        .summary-row:last-child {{ margin-bottom: 0; }}
        .summary-label {{ color: #666; }}
        .summary-value {{ font-weight: 600; color: #333; }}
        .amount-total {{ font-size: 32px; font-weight: 900; color: #667eea; text-align: center; margin-top: 16px; }}
        
        .test-note {{ background: #fffbea; border-left: 4px solid #f59e0b; padding: 12px; 
                      border-radius: 4px; margin-bottom: 24px; font-size: 13px; color: #92400e; }}
        
        .button-group {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
        button {{ padding: 14px 20px; border: none; border-radius: 8px; font-size: 16px; 
                  font-weight: 600; cursor: pointer; transition: all 0.2s; }}
        
        .btn-success {{ background: #10b981; color: white; }}
        .btn-success:hover {{ background: #059669; }}
        
        .btn-failed {{ background: #ef4444; color: white; }}
        .btn-failed:hover {{ background: #dc2626; }}
        
        .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #999; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="logo">💳 PayMongo (Mock)</div>
        <h1 class="title">Checkout</h1>
        <div class="divider"></div>
    </div>
    
    <div class="payment-summary">
        <div class="summary-row">
            <span class="summary-label">Reference:</span>
            <span class="summary-value">{ref}</span>
        </div>
        <div class="summary-row">
            <span class="summary-label">Link ID:</span>
            <span class="summary-value" style="font-size: 12px; font-family: monospace;">{link_id[-12:]}</span>
        </div>
        <div class="amount-total">₱{amount_pesos:,.2f}</div>
    </div>
    
    <div class="test-note">
        ⚠️ <strong>TEST MODE:</strong> This is a mock PayMongo checkout page. 
        Choose below to test payment success or failure.
    </div>
    
    <div class="button-group">
        <button class="btn-success" onclick="completePayment('success')">✓ Pay Successfully</button>
        <button class="btn-failed" onclick="completePayment('failed')">✗ Simulate Failure</button>
    </div>
    
    <div class="footer">
        🔒 This is a test environment. No real charges will be made.
    </div>
</div>

<script>
function completePayment(result) {{
    const url = '/payment/mock-complete?link_id={link_id}&ref={ref}&result=' + result;
    window.location.href = url;
}}
</script>
</body>
</html>
"""

@app.route('/payment/mock-complete')
def mock_payment_complete():
    """
    Handle mock payment result and redirect to callback.
    """
    link_id = request.args.get('link_id', '').strip()
    ref = request.args.get('ref', '').strip()
    result = request.args.get('result', 'pending').strip()
    
    if not link_id or not ref:
        return "Invalid parameters", 400
    
    db = get_db()
    try:
        # Update mock link status
        status = 'paid' if result == 'success' else 'failed'
        execute(db, """
            UPDATE paymongo_mock_links SET status=%s, updated_at=NOW()
            WHERE link_id=%s
        """, (status, link_id))
        db.commit()
        
        print(f"[OK] Mock payment completed: {link_id} -> {status}")
    except Exception as e:
        print(f"[ERROR] Error updating mock payment: {str(e)}")
    finally:
        db.close()
    
    # Redirect to callback (which will verify status and update booking)
    return redirect(url_for('paymongo_callback', ref=ref))

@app.route('/payment/paymongo-callback')
@login_required
def paymongo_callback():
    """
    PayMongo (or mock) redirects here after checkout.
    We verify the link status and update our DB accordingly.
    """
    ref_code = request.args.get('ref', '').strip()
    if not ref_code:
        flash('Invalid payment reference.', 'error')
        return redirect(url_for('booking'))

    db = get_db()
    try:
        pay_row = query(db,
            "SELECT paymongo_link_id FROM payments WHERE booking_ref=%s ORDER BY id DESC LIMIT 1",
            (ref_code,), one=True)

        if pay_row and pay_row.get('paymongo_link_id'):
            try:
                link_status = verify_paymongo_link(pay_row['paymongo_link_id'])
                
                # ✅ Process both REAL and MOCK links
                if link_status == 'paid':
                    bookings_list = query(db,
                        "SELECT id, seat_id, showing_id FROM bookings WHERE ref_code=%s", (ref_code,))
                    for b in bookings_list:
                        execute(db, "UPDATE seats SET status='booked', locked_until=NULL WHERE id=%s",
                                (b['seat_id'],))
                    execute(db,
                        "UPDATE bookings SET payment_status='paid', status='Confirmed' WHERE ref_code=%s",
                        (ref_code,))
                    execute(db,
                        "UPDATE payments SET status='paid', paid_at=NOW() WHERE booking_ref=%s",
                        (ref_code,))
                    if bookings_list:
                        avail = query(db,
                            "SELECT COUNT(*) AS cnt FROM seats WHERE showing_id=%s AND status='available'",
                            (bookings_list[0]['showing_id'],), one=True)
                        if avail and avail['cnt'] == 0:
                            execute(db, "UPDATE showings SET status='full' WHERE id=%s",
                                    (bookings_list[0]['showing_id'],))
                    db.commit()
                    print(f"✅ Payment confirmed for {ref_code}")
                elif link_status == 'failed':
                    bookings_list = query(db,
                        "SELECT id, seat_id FROM bookings WHERE ref_code=%s", (ref_code,))
                    for b in bookings_list:
                        execute(db,
                            "UPDATE seats SET status='available', locked_until=NULL WHERE id=%s",
                            (b['seat_id'],))
                    execute(db,
                        "UPDATE bookings SET payment_status='failed', status='Cancelled' WHERE ref_code=%s",
                        (ref_code,))
                    execute(db,
                        "UPDATE payments SET status='failed', failed_at=NOW() WHERE booking_ref=%s",
                        (ref_code,))
                    db.commit()
                    print(f"❌ Payment failed for {ref_code}")
            except Exception as e:
                print(f"⚠️ Error verifying payment: {str(e)}")
                pass
    finally:
        db.close()
    
    return redirect(url_for('payment_result', ref=ref_code))

# ─────────────────────────────────────────────────────────────
#  MY BOOKINGS
# ─────────────────────────────────────────────────────────────
@app.route('/my-bookings')
@login_required
def my_bookings():
    # Redirect admins - they don't have bookings, only users do
    if session.get('is_admin'):
        flash('Admins cannot view user bookings. Use admin dashboard instead.', 'warning')
        return redirect(url_for('admin_dashboard'))
    
    # Ensure user_id exists in session
    if 'user_id' not in session:
        flash('Please log in to view your bookings.', 'warning')
        return redirect(url_for('login'))
    
    db   = get_db()
    try:
        rows = query(db, """
            SELECT b.ref_code, b.ticket_type, b.unit_price, b.status AS booking_status,
                   b.created_at, b.customer_name, b.contact,
                   b.discount_status, b.payment_status,
                   st.seat_code, st.category,
                   m.title AS movie, c.name AS cinema,
                   s.show_date, s.show_time
            FROM bookings b
            JOIN seats    st ON st.id  = b.seat_id
            JOIN showings s  ON s.id   = b.showing_id
            JOIN movies   m  ON m.id   = s.movie_id
            JOIN cinemas  c  ON c.id   = s.cinema_id
            WHERE b.user_id = %s
            ORDER BY 
                CASE 
                    WHEN b.payment_status = 'paid' THEN 0
                    ELSE 1
                END,
                b.created_at DESC
        """, (session['user_id'],))
    except Exception as e:
        print(f"❌ Error loading bookings for user {session.get('user_id')}: {str(e)}")
        flash(f'Error loading bookings: {str(e)}', 'error')
        rows = []
    finally:
        db.close()

    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['ref_code']].append(dict(r))

    bookings_list = []
    for ref, seats in grouped.items():
        first = seats[0]
        total = sum(s['unit_price'] for s in seats)
        d_obj = first['show_date']
        date_fmt = d_obj.strftime('%b %d, %Y') if hasattr(d_obj, 'strftime') else str(d_obj)
        bookings_list.append({
            'ref':             ref,
            'movie':           first['movie'],
            'cinema':          first['cinema'],
            'date':            date_fmt,
            'showtime':        _fmt_time(first['show_time']),
            'seats':           ', '.join(s['seat_code'] for s in seats),
            'ticket_type':     first['ticket_type'],
            'total':           total,
            'status':          first['booking_status'],
            'booked_on':       first['created_at'],
            'discount_status': first['discount_status'],
            'payment_status':  first['payment_status'],
        })

    return render_template('my_bookings.html',
                           user_name=session.get('user_name') or session.get('admin_name', 'Admin'),
                           bookings=bookings_list)

# ─────────────────────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))

    errors = {}
    form = {}
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password   = request.form.get('password',   '').strip()
        form = {'identifier': identifier}

        if not identifier:
            errors['identifier'] = 'Email or mobile is required.'
        elif not is_valid_email(identifier) and not is_valid_phone(identifier):
            errors['identifier'] = 'Enter a valid email or PH mobile (09XXXXXXXXX).'
        if not password:
            errors['password'] = 'Password is required.'
        elif len(password) < 6:
            errors['password'] = 'Min 6 characters.'

        if not errors:
            if identifier == ADMIN_EMAIL and password == ADMIN_PASSWORD:
                session['is_admin']   = True
                session['admin_name'] = 'Admin'
                return redirect(url_for('admin_dashboard'))
            try:
                db   = get_db()
                user = query(db,
                    'SELECT * FROM users WHERE email=%s OR mobile=%s',
                    (identifier, identifier), one=True)
                db.close()
                if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
                    session['user_id']   = user['id']
                    session['user_name'] = user['full_name']
                    flash(f'Welcome back, {user["full_name"]}!', 'success')
                    return redirect(url_for('index'))
                else:
                    errors['general'] = 'Invalid credentials. Please try again.'
            except Exception as e:
                errors['general'] = f'Database error: {e}'

    return render_template('login.html', errors=errors, form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
    errors = {}
    form = {}
    if request.method == 'POST':
        identifier = request.form.get('identifier',       '').strip()
        full_name  = request.form.get('full_name',        '').strip()
        age        = request.form.get('age',              '').strip()
        gender     = request.form.get('gender',           '').strip()
        province   = request.form.get('province',         '').strip()
        city       = request.form.get('city',             '').strip()
        barangay   = request.form.get('barangay',         '').strip()
        password   = request.form.get('password',         '').strip()
        confirm_pw = request.form.get('confirm_password', '').strip()
        form = dict(identifier=identifier, full_name=full_name, age=age,
                    gender=gender, province=province, city=city, barangay=barangay)

        if not identifier:
            errors['identifier'] = 'Required.'
        elif not is_valid_email(identifier) and not is_valid_phone(identifier):
            errors['identifier'] = 'Enter valid email or 09XXXXXXXXX.'
        if not full_name:
            errors['full_name'] = 'Required.'
        elif len(full_name) < 2:
            errors['full_name'] = 'Min 2 chars.'
        if not age:
            errors['age'] = 'Required.'
        elif not age.isdigit() or not (1 <= int(age) <= 120):
            errors['age'] = 'Enter valid age (1-120).'
        if not gender:
            errors['gender'] = 'Select gender.'
        if not province:
            errors['province'] = 'Select province.'
        if not city:
            errors['city'] = 'Select city.'
        if not barangay:
            errors['barangay'] = 'Select barangay.'
        if not password:
            errors['password'] = 'Required.'
        elif len(password) < 6:
            errors['password'] = 'Min 6 chars.'
        elif not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
            errors['password'] = 'Must contain letters and numbers.'
        if not confirm_pw:
            errors['confirm_password'] = 'Confirm your password.'
        elif password != confirm_pw:
            errors['confirm_password'] = 'Passwords do not match.'

        if not errors:
            try:
                db     = get_db()
                email  = identifier if is_valid_email(identifier) else None
                mobile = identifier if is_valid_phone(identifier) else None
                exists = query(db,
                    'SELECT id FROM users WHERE email=%s OR mobile=%s',
                    (email, mobile), one=True)
                if exists:
                    errors['identifier'] = 'Already registered. Please log in.'
                else:
                    hashed  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                    address = f"{barangay}, {city}, {province}"
                    execute(db, """
                        INSERT INTO users
                            (email, mobile, full_name, age, gender, address, password)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (email, mobile, full_name, int(age), gender, address, hashed))
                    db.commit()
                    flash(f'Welcome, {full_name}! Account created. Please log in.', 'success')
                    return redirect(url_for('login'))
                db.close()
            except Exception as e:
                errors['general'] = f'Database error: {e}'

    return render_template('register.html', errors=errors, form=form)

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('landing'))

# ─────────────────────────────────────────────────────────────
#  ADMIN — DASHBOARD
# ─────────────────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == 'POST':
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '').strip()
        if u == ADMIN_EMAIL and p == ADMIN_PASSWORD:
            session['is_admin']   = True
            session['admin_name'] = 'Admin'
            return redirect(url_for('admin_dashboard'))
        else:
            error = 'Invalid admin credentials.'
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    session.pop('admin_name', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    try:
        stats = {
            'available_seats':    query(db, "SELECT COUNT(*) AS n FROM seats WHERE status='available'", one=True)['n'],
            'total_sales':        query(db, "SELECT COALESCE(SUM(total_price),0) AS n FROM bookings WHERE status IN ('Confirmed','Completed') AND payment_status='paid'", one=True)['n'],
            'total_bookings':     query(db, "SELECT COUNT(*) AS n FROM bookings", one=True)['n'],
            'confirmed_bookings': query(db, "SELECT COUNT(*) AS n FROM bookings WHERE status='Confirmed' AND payment_status='paid'", one=True)['n'],
            'active_movies':      query(db, "SELECT COUNT(*) AS n FROM movies WHERE status='active'", one=True)['n'],
            'total_movies':       query(db, "SELECT COUNT(*) AS n FROM movies", one=True)['n'],
            'total_users':        query(db, "SELECT COUNT(*) AS n FROM users", one=True)['n'],
            'today_showings':     query(db, "SELECT COUNT(*) AS n FROM showings WHERE show_date=%s AND status IN ('open','full')",
                                       (date.today().isoformat(),), one=True)['n'],
            'pending_payments':   query(db, "SELECT COUNT(*) AS n FROM payments WHERE status='pending'", one=True)['n'],
            'total_halls':        query(db, "SELECT COUNT(*) AS n FROM cinema_halls", one=True)['n'],
        }

        recent_bookings = query(db, """
            SELECT b.id, b.booking_ref, b.customer_name, b.total_price, b.status,
                   b.ticket_count, b.ticket_type, b.seat_codes,
                   b.payment_status, b.discount_status,
                   m.title AS movie_title, s.show_date, s.show_time
            FROM bookings b
            JOIN showings s ON b.showing_id=s.id
            JOIN movies   m ON s.movie_id=m.id
            ORDER BY b.id DESC LIMIT 10
        """)

        active_movies = query(db, """
            SELECT m.*, COALESCE((SELECT COUNT(*) FROM showings sh
                                  JOIN seats st ON st.showing_id=sh.id
                                 WHERE sh.movie_id=m.id AND st.status='available'),0) AS avail_seats
            FROM movies m WHERE m.status='active' ORDER BY m.title
        """)
    finally:
        db.close()
    return render_template('admin_dashboard.html',
                           stats=stats,
                           recent_bookings=recent_bookings,
                           active_movies=active_movies)

# ─────────────────────────────────────────────────────────────
#  ADMIN — CINEMA HALLS & SEAT LAYOUT EDITOR
# ─────────────────────────────────────────────────────────────
@app.route('/admin/halls')
@admin_required
def admin_halls():
    db = get_db()
    try:
        cinemas_list = query(db, "SELECT * FROM cinemas ORDER BY name")
        halls = query(db, """
            SELECT h.id, h.cinema_id, h.hall_name, h.rows_count, h.cols_count, h.created_at,
                   c.name AS cinema_name,
                   (SELECT COUNT(*) FROM hall_seat_config hsc WHERE hsc.hall_id=h.id) AS seat_count,
                   (SELECT COUNT(*) FROM hall_seat_config hsc WHERE hsc.hall_id=h.id AND hsc.seat_type='VIP') AS vip_count,
                   (SELECT COUNT(*) FROM hall_seat_config hsc WHERE hsc.hall_id=h.id AND hsc.seat_type='PWD') AS pwd_count,
                   (SELECT COUNT(*) FROM hall_seat_config hsc WHERE hsc.hall_id=h.id AND hsc.is_active=0) AS inactive_count
            FROM cinema_halls h
            JOIN cinemas c ON c.id=h.cinema_id
            ORDER BY c.name, h.hall_name
        """)
    finally:
        db.close()
    return render_template('admin_halls.html', halls=halls, cinemas=cinemas_list)

@app.route('/admin/halls/add', methods=['POST'])
@admin_required
def admin_halls_add():
    cinema_id  = request.form.get('cinema_id', type=int)
    hall_name  = request.form.get('hall_name', '').strip()
    rows_count = request.form.get('rows_count', type=int) or 8
    cols_count = request.form.get('cols_count', type=int) or 10

    if not cinema_id or not hall_name:
        flash('Cinema and hall name are required.', 'error')
        return redirect(url_for('admin_halls'))

    rows_count = max(2, min(26, rows_count))
    cols_count = max(2, min(30, cols_count))
    row_labels = list(string.ascii_uppercase)

    try:
        db = get_db()
        hall_id = execute(db, """
            INSERT INTO cinema_halls (cinema_id, hall_name, rows_count, cols_count)
            VALUES (%s,%s,%s,%s)
        """, (cinema_id, hall_name, rows_count, cols_count))

        # Auto-seed: rows A-B = VIP, rest = Regular
        for r_idx in range(rows_count):
            rl = row_labels[r_idx]
            seat_type = 'VIP' if r_idx < 2 else 'Regular'
            for col in range(1, cols_count + 1):
                execute(db, """
                    INSERT IGNORE INTO hall_seat_config
                        (hall_id, row_label, col_number, seat_code, seat_type, is_active)
                    VALUES (%s,%s,%s,%s,%s,1)
                """, (hall_id, rl, col, f"{rl}{col}", seat_type))

        db.commit()
        flash(f'Hall "{hall_name}" created! Customize the layout below.', 'success')
        return redirect(url_for('admin_seat_editor', hall_id=hall_id))
    except Exception as e:
        flash(f'Error creating hall: {e}', 'error')
        return redirect(url_for('admin_halls'))
    finally:
        db.close()

@app.route('/admin/halls/<int:hall_id>/editor')
@admin_required
def admin_seat_editor(hall_id):
    db = get_db()
    try:
        hall = query(db, """
            SELECT h.*, c.name AS cinema_name
            FROM cinema_halls h
            JOIN cinemas c ON c.id=h.cinema_id
            WHERE h.id=%s
        """, (hall_id,), one=True)

        if not hall:
            flash('Hall not found.', 'error')
            return redirect(url_for('admin_halls'))

        seats = query(db,
            "SELECT * FROM hall_seat_config WHERE hall_id=%s ORDER BY row_label, col_number",
            (hall_id,))
    finally:
        db.close()

    # Build lookup dict keyed by "row-col"
    seat_map = {f"{s['row_label']}-{s['col_number']}": dict(s) for s in seats} if seats else {}

    row_labels  = list(string.ascii_uppercase[:hall['rows_count']])
    col_numbers = list(range(1, hall['cols_count'] + 1))

    return render_template('admin_seat_editor.html',
                           hall=hall,
                           seat_map=seat_map,
                           row_labels=row_labels,
                           col_numbers=col_numbers)

@app.route('/admin/halls/<int:hall_id>/save-layout', methods=['POST'])
@admin_required
def admin_halls_save_layout(hall_id):
    data = request.get_json(force=True)
    seats_data = data.get('seats', [])

    if not seats_data:
        return jsonify({'ok': False, 'msg': 'No seat data received.'})

    db = get_db()
    try:
        execute(db, "DELETE FROM hall_seat_config WHERE hall_id=%s", (hall_id,))
        for s in seats_data:
            execute(db, """
                INSERT INTO hall_seat_config
                    (hall_id, row_label, col_number, seat_code, seat_type, is_active)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (hall_id, s['row'], int(s['col']), s['code'],
                  s['type'], 1 if s['active'] else 0))
        db.commit()
        return jsonify({'ok': True, 'msg': f'Layout saved ({len(seats_data)} seats).'})
    except Exception as e:
        try:
            db.rollback()
        except:
            pass
        return jsonify({'ok': False, 'msg': str(e)})
    finally:
        db.close()

@app.route('/admin/halls/delete', methods=['POST'])
@admin_required
def admin_halls_delete():
    hall_id = request.form.get('hall_id', type=int)
    if not hall_id:
        flash('Invalid hall.', 'error')
        return redirect(url_for('admin_halls'))
    try:
        db = get_db()
        execute(db, "DELETE FROM hall_seat_config WHERE hall_id=%s", (hall_id,))
        execute(db, "DELETE FROM cinema_halls WHERE id=%s", (hall_id,))
        db.commit()
        flash('Hall deleted.', 'success')
    except Exception as e:
        flash(f'Error deleting hall: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_halls'))


# ─────────────────────────────────────────────────────────────
#  ADMIN — ASSIGN MOVIE TO HALL  (creates showings)
# ─────────────────────────────────────────────────────────────
@app.route('/admin/halls/<int:hall_id>/showings')
@admin_required
def admin_hall_showings(hall_id):
    """Show all showings for this hall so admin can manage assignments."""
    db = get_db()
    try:
        hall = query(db, """
            SELECT h.*, c.name AS cinema_name, c.id AS cinema_id
            FROM cinema_halls h
            JOIN cinemas c ON c.id = h.cinema_id
            WHERE h.id = %s
        """, (hall_id,), one=True)

        if not hall:
            flash('Hall not found.', 'error')
            return redirect(url_for('admin_halls'))

        showings = query(db, """
            SELECT s.id, s.show_date, s.show_time, s.status,
                   m.title AS movie_title, m.id AS movie_id,
                   (SELECT COUNT(*) FROM seats st WHERE st.showing_id = s.id AND st.status = 'available') AS avail,
                   (SELECT COUNT(*) FROM seats st WHERE st.showing_id = s.id AND st.status = 'booked')    AS booked,
                   (SELECT COUNT(*) FROM seats st WHERE st.showing_id = s.id)                             AS total_seats_seeded
            FROM showings s
            JOIN movies m ON m.id = s.movie_id
            WHERE s.hall_id = %s
            ORDER BY s.show_date DESC, s.show_time
        """, (hall_id,))

        movies = query(db, "SELECT id, title FROM movies WHERE status='active' ORDER BY title")
    finally:
        db.close()
    return render_template('admin_hall_showings.html',
                           hall=hall, showings=showings, movies=movies,
                           today=date.today().isoformat())


@app.route('/admin/halls/<int:hall_id>/assign-movie', methods=['POST'])
@admin_required
def admin_hall_assign_movie(hall_id):
    """Create one or more showings for a hall by assigning a movie + dates/times."""
    movie_id   = request.form.get('movie_id', type=int)
    show_dates = request.form.getlist('show_dates')   # multiple dates allowed
    show_times = request.form.getlist('show_times')   # multiple times allowed

    if not movie_id or not show_dates or not show_times:
        flash('Movie, at least one date, and at least one showtime are required.', 'error')
        return redirect(url_for('admin_hall_showings', hall_id=hall_id))

    db = get_db()
    try:
        hall = query(db, "SELECT cinema_id FROM cinema_halls WHERE id=%s", (hall_id,), one=True)
        if not hall:
            flash('Hall not found.', 'error')
            return redirect(url_for('admin_halls'))

        created = 0
        skipped = 0
        for sd in show_dates:
            for st in show_times:
                try:
                    existing = query(db, """
                        SELECT id FROM showings
                        WHERE hall_id=%s AND show_date=%s AND show_time=%s
                    """, (hall_id, sd, st), one=True)
                    if existing:
                        skipped += 1
                        continue
                    showing_id = execute(db, """
                        INSERT INTO showings (movie_id, cinema_id, hall_id, show_date, show_time, status)
                        VALUES (%s, %s, %s, %s, %s, 'open')
                    """, (movie_id, hall['cinema_id'], hall_id, sd, st))
                    # Immediately seed seats from this hall's layout
                    seed_seats_from_hall(db, showing_id, hall_id)
                    created += 1
                except Exception:
                    skipped += 1
        db.commit()

        if created:
            flash(f'{created} showing(s) created successfully! {skipped} skipped (already exist).', 'success')
        else:
            flash(f'No new showings created. {skipped} already existed.', 'warning')

    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        db.close()

    return redirect(url_for('admin_hall_showings', hall_id=hall_id))


@app.route('/admin/halls/showings/reseed', methods=['POST'])
@admin_required
def admin_hall_showing_reseed():
    showing_id = request.form.get('showing_id', type=int)
    hall_id    = request.form.get('hall_id',    type=int)
    if showing_id and hall_id:
        try:
            db = get_db()
            seed_seats_from_hall(db, showing_id, hall_id)
            db.close()
            flash('Seats seeded from hall layout.', 'success')
        except Exception as e:
            flash(f'Error seeding seats: {e}', 'error')
    return redirect(url_for('admin_hall_showings', hall_id=hall_id))

@app.route('/admin/halls/showings/delete', methods=['POST'])
@admin_required
def admin_hall_showing_delete():
    """Delete a showing and free its seats."""
    showing_id = request.form.get('showing_id', type=int)
    hall_id    = request.form.get('hall_id',    type=int)
    if not showing_id:
        flash('Invalid showing.', 'error')
        return redirect(url_for('admin_halls'))
    try:
        db = get_db()
        # Cancel confirmed bookings first
        execute(db, "UPDATE bookings SET status='Cancelled' WHERE showing_id=%s", (showing_id,))
        execute(db, "DELETE FROM seats    WHERE showing_id=%s", (showing_id,))
        execute(db, "DELETE FROM showings WHERE id=%s",         (showing_id,))
        db.commit()
        flash('Showing deleted.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_hall_showings', hall_id=hall_id))


# ─────────────────────────────────────────────────────────────
#  ADMIN — MOVIES
# ─────────────────────────────────────────────────────────────
@app.route('/admin/movies')
@admin_required
def admin_movies():
    db = get_db()
    try:
        movies_list = query(db, """
            SELECT m.*,
                   COALESCE((SELECT COUNT(*) FROM showings sh
                             JOIN seats st ON st.showing_id = sh.id
                            WHERE sh.movie_id = m.id
                              AND st.status = 'available'), 0) AS avail_seats
            FROM movies m
            ORDER BY m.created_at DESC
        """)
    finally:
        db.close()
    return render_template('admin_movies.html', movies=movies_list)

@app.route('/admin/movies/add', methods=['POST'])
@admin_required
def admin_movies_add():
    title        = request.form.get('title', '').strip()
    genre        = request.form.get('genre', '').strip()
    cast_members = request.form.get('cast_members', '').strip()
    duration     = request.form.get('duration_mins', '120').strip()
    price        = request.form.get('price', '450').strip() or '450'
    rating       = request.form.get('rating', '0').strip() or '0'
    release_date = request.form.get('release_date', '').strip() or None
    status_val   = request.form.get('status', 'active')
    description  = request.form.get('description', '').strip()

    if not title or not genre or not duration:
        flash('Title, genre, and duration are required.', 'error')
        return redirect(url_for('admin_movies'))
    
    # ✅ Validate price
    try:
        price_int = int(price)
        if price_int < 1 or price_int > 9999:
            flash('Price must be between 1 and 9999.', 'error')
            return redirect(url_for('admin_movies'))
    except ValueError:
        flash('Price must be a valid number.', 'error')
        return redirect(url_for('admin_movies'))

    poster_path = 'images/no_poster.png'
    poster_file = request.files.get('poster')
    if poster_file and poster_file.filename and allowed_file(poster_file.filename):
        from werkzeug.utils import secure_filename
        filename = secure_filename(poster_file.filename)
        save_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'movies')
        os.makedirs(save_dir, exist_ok=True)
        poster_file.save(os.path.join(save_dir, filename))
        poster_path = f'images/movies/{filename}'

    try:
        db = get_db()
        execute(db, """
            INSERT INTO movies
                (title, genre, cast_members, duration_mins, price, rating,
                 release_date, status, description, poster_path)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (title, genre, cast_members, int(duration), int(price), float(rating),
              release_date, status_val, description, poster_path))
        db.commit()
        flash(f'Movie "{title}" added!', 'success')
    except Exception as e:
        flash(f'Error adding movie: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_movies'))

@app.route('/admin/movies/edit/<int:movie_id>', methods=['POST'])
@admin_required
def admin_movies_edit(movie_id):
    title        = request.form.get('title', '').strip()
    genre        = request.form.get('genre', '').strip()
    cast_members = request.form.get('cast_members', '').strip()
    duration     = request.form.get('duration_mins', '120').strip()
    price        = request.form.get('price', '450').strip() or '450'
    rating       = request.form.get('rating', '0').strip() or '0'
    release_date = request.form.get('release_date', '').strip() or None
    status_val   = request.form.get('status', 'active')
    description  = request.form.get('description', '').strip()

    if not title or not genre or not duration:
        flash('Title, genre, and duration are required.', 'error')
        return redirect(url_for('admin_movies'))
    
    # ✅ Validate price
    try:
        price_int = int(price)
        if price_int < 1 or price_int > 9999:
            flash('Price must be between 1 and 9999.', 'error')
            return redirect(url_for('admin_movies'))
    except ValueError:
        flash('Price must be a valid number.', 'error')
        return redirect(url_for('admin_movies'))
    try:
        db = get_db()
        poster_file = request.files.get('poster')
        if poster_file and poster_file.filename and allowed_file(poster_file.filename):
            from werkzeug.utils import secure_filename
            filename = secure_filename(poster_file.filename)
            save_dir = os.path.join(os.path.dirname(__file__), 'static', 'images', 'movies')
            os.makedirs(save_dir, exist_ok=True)
            poster_file.save(os.path.join(save_dir, filename))
            execute(db, """
                UPDATE movies SET title=%s,genre=%s,cast_members=%s,duration_mins=%s,price=%s,
                                  rating=%s,release_date=%s,status=%s,description=%s,
                                  poster_path=%s WHERE id=%s
            """, (title, genre, cast_members, int(duration), int(price), float(rating),
                  release_date, status_val, description, f'images/movies/{filename}', movie_id))
        else:
            execute(db, """
                UPDATE movies SET title=%s,genre=%s,cast_members=%s,duration_mins=%s,price=%s,
                                  rating=%s,release_date=%s,status=%s,description=%s WHERE id=%s
            """, (title, genre, cast_members, int(duration), int(price), float(rating),
                  release_date, status_val, description, movie_id))
        db.commit()
        flash(f'Movie "{title}" updated!', 'success')
    except Exception as e:
        flash(f'Error updating movie: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_movies'))

@app.route('/admin/movies/delete', methods=['POST'])
@admin_required
def admin_movies_delete():
    movie_id = request.form.get('movie_id', type=int)
    if not movie_id:
        flash('Invalid movie.', 'error')
        return redirect(url_for('admin_movies'))
    try:
        db = get_db()
        movie = query(db, "SELECT title FROM movies WHERE id=%s", (movie_id,), one=True)
        if movie:
            execute(db, """
                DELETE seats FROM seats
                  JOIN showings ON showings.id = seats.showing_id
                WHERE showings.movie_id=%s
            """, (movie_id,))
            execute(db, """
                DELETE bookings FROM bookings
                  JOIN showings ON showings.id = bookings.showing_id
                WHERE showings.movie_id=%s
            """, (movie_id,))
            execute(db, "DELETE FROM showings WHERE movie_id=%s", (movie_id,))
            execute(db, "DELETE FROM movies WHERE id=%s", (movie_id,))
            db.commit()
            flash(f'Movie "{movie["title"]}" deleted.', 'success')
    except Exception as e:
        flash(f'Error deleting movie: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_movies'))

# ─────────────────────────────────────────────────────────────
#  ADMIN — BOOKINGS
# ─────────────────────────────────────────────────────────────
@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    db = get_db()
    try:
        bookings_list = query(db, """
            SELECT b.id, b.booking_ref, b.customer_name, b.contact, b.total_price,
                   b.status, b.ticket_count, b.ticket_type, b.seat_codes,
                   b.discount_status, b.payment_status,
                   b.created_at,
                   m.title AS movie_title, c.name AS cinema_name,
                   h.hall_name,
                   s.show_date, s.show_time
            FROM bookings b
            JOIN showings s ON b.showing_id=s.id
            JOIN movies   m ON s.movie_id=m.id
            JOIN cinemas  c ON s.cinema_id=c.id
            LEFT JOIN cinema_halls h ON s.hall_id=h.id
            ORDER BY b.id DESC
            LIMIT 100
        """)
    finally:
        db.close()
    return render_template('admin_bookings.html', bookings=bookings_list)

@app.route('/admin/bookings/cancel', methods=['POST'])
@admin_required
def admin_bookings_cancel():
    ref_code = request.form.get('ref_code', '').strip()
    if not ref_code:
        flash('Invalid booking reference.', 'error')
        return redirect(url_for('admin_bookings'))
    try:
        db = get_db()
        execute(db, "UPDATE bookings SET status='Cancelled' WHERE ref_code=%s", (ref_code,))
        seat_rows = query(db, "SELECT seat_id FROM bookings WHERE ref_code=%s", (ref_code,))
        for s in seat_rows:
            execute(db,
                "UPDATE seats SET status='available', locked_until=NULL WHERE id=%s", (s['seat_id'],))
        execute(db, "UPDATE payments SET status='refunded' WHERE booking_ref=%s", (ref_code,))
        db.commit()
        flash(f'Booking {ref_code} cancelled.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_bookings'))

@app.route('/admin/bookings/mark-paid', methods=['POST'])
@admin_required
def admin_bookings_mark_paid():
    """Mark a walk-in booking as paid by admin at counter."""
    ref_code = request.form.get('ref_code', '').strip()
    if not ref_code:
        flash('Invalid booking reference.', 'error')
        return redirect(url_for('admin_bookings'))
    try:
        db = get_db()
        # Update booking to mark as paid
        execute(db, """
            UPDATE bookings 
            SET payment_status='paid', status='Confirmed'
            WHERE ref_code=%s AND payment_status='walkin_pending'
        """, (ref_code,))
        db.commit()
        flash(f'✅ Booking {ref_code} marked as PAID at counter.', 'success')
    except Exception as e:
        flash(f'Error updating payment status: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_bookings'))

# ─────────────────────────────────────────────────────────────
#  ADMIN — PAYMENTS
# ─────────────────────────────────────────────────────────────
@app.route('/admin/payments')
@admin_required
def admin_payments():
    db = get_db()
    try:
        payments = query(db, """
            SELECT p.*, b.customer_name, b.ticket_type, b.ref_code, b.payment_status,
                   m.title AS movie
            FROM payments p
            LEFT JOIN bookings b ON b.ref_code=p.booking_ref
            LEFT JOIN showings s ON s.id=b.showing_id
            LEFT JOIN movies   m ON m.id=s.movie_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
            LIMIT 100
        """)
    finally:
        db.close()
    return render_template('admin_payments.html', payments=payments)

@app.route('/admin/payments/walkin-complete', methods=['POST'])
@admin_required
def admin_walkin_complete():
    """Mark a walk-in payment as completed (customer paid at counter)."""
    ref_code = request.form.get('ref_code', '').strip()
    if not ref_code:
        return jsonify({'ok': False, 'msg': 'Invalid booking reference.'}), 400
    
    try:
        db = get_db()
        booking = query(db, "SELECT payment_status FROM bookings WHERE ref_code=%s LIMIT 1",
                       (ref_code,), one=True)
        if not booking:
            db.close()
            return jsonify({'ok': False, 'msg': 'Booking not found.'}), 404
        
        # Update booking payment status to paid
        execute(db, "UPDATE bookings SET payment_status='paid', status='Completed' WHERE ref_code=%s",
                (ref_code,))
        
        # Create payment record if not exists
        existing_payment = query(db, "SELECT id FROM payments WHERE booking_ref=%s",
                                (ref_code,), one=True)
        if not existing_payment:
            booking_data = query(db, "SELECT total_price FROM bookings WHERE ref_code=%s",
                               (ref_code,), one=True)
            if booking_data:
                execute(db, """
                    INSERT INTO payments (booking_ref, amount, payment_method, status, paid_at)
                    VALUES (%s, %s, 'walkin_counter_payment', 'paid', NOW())
                """, (ref_code, booking_data['total_price']))
        else:
            execute(db, "UPDATE payments SET status='paid', paid_at=NOW() WHERE booking_ref=%s",
                   (ref_code,))
        
        db.commit()
        db.close()
        flash(f'Walk-in payment {ref_code} marked as completed.', 'success')
        return jsonify({'ok': True, 'msg': f'Payment for {ref_code} completed.'}), 200
    except Exception as e:
        try:
            db.rollback()
            db.close()
        except:
            pass
        flash(f'Error completing payment: {str(e)}', 'error')
        return jsonify({'ok': False, 'msg': f'Error: {str(e)}'}), 500

# ─────────────────────────────────────────────────────────────
#  ADMIN — USERS
# ─────────────────────────────────────────────────────────────
@app.route('/admin/users')
@admin_required
def admin_users():
    db = get_db()
    try:
        users_list = query(db, """
            SELECT u.*,
                   COALESCE((SELECT COUNT(*) FROM bookings b WHERE b.user_id=u.id),0) AS booking_count
            FROM users u
            WHERE u.email != %s OR u.email IS NULL
            ORDER BY u.id DESC
            LIMIT 100
        """, (ADMIN_EMAIL,))
    finally:
        db.close()
    return render_template('admin_users.html', users=users_list)

@app.route('/admin/users/delete', methods=['POST'])
@admin_required
def admin_users_delete():
    user_id = request.form.get('user_id', type=int)
    if not user_id:
        flash('Invalid user.', 'error')
        return redirect(url_for('admin_users'))
    try:
        db = get_db()
        user = query(db, "SELECT full_name FROM users WHERE id=%s", (user_id,), one=True)
        if user:
            execute(db, "DELETE FROM bookings WHERE user_id=%s", (user_id,))
            execute(db, "DELETE FROM users WHERE id=%s", (user_id,))
            db.commit()
            flash(f'User "{user["full_name"]}" deleted.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_users'))

# ─────────────────────────────────────────────────────────────
#  PROFILE PAGE
# ─────────────────────────────────────────────────────────────
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    # Admin profile
    if session.get('is_admin'):
        db = get_db()
        try:
            movies = get_movies_with_status(db)
        finally:
            db.close()
        return render_template('profile.html',
            user_name='Admin',
            user=None, is_admin=True,
            bookings=[], errors={}, form={}, all_movies=movies)

    # Ensure user_id exists in session for regular users
    if 'user_id' not in session:
        flash('Please log in to view your profile.', 'warning')
        return redirect(url_for('login'))

    db  = get_db()
    uid = session['user_id']
    errors = {}
    form = {}

    try:
        if request.method == 'POST':
            action = request.form.get('action', 'update')

            if action == 'update':
                full_name = request.form.get('full_name', '').strip()
                age       = request.form.get('age', '').strip()
                gender    = request.form.get('gender', '').strip()
                address   = request.form.get('address', '').strip()
                form = dict(full_name=full_name, age=age, gender=gender, address=address)

                if not full_name or len(full_name) < 2:
                    errors['full_name'] = 'Full name required (min 2 chars).'
                if not age or not age.isdigit() or not (1 <= int(age) <= 120):
                    errors['age'] = 'Enter a valid age (1–120).'
                if not gender:
                    errors['gender'] = 'Please select a gender.'

                if not errors:
                    execute(db, """
                        UPDATE users SET full_name=%s, age=%s, gender=%s, address=%s WHERE id=%s
                    """, (full_name, int(age), gender, address, uid))
                    db.commit()
                    session['user_name'] = full_name
                    flash('Profile updated successfully!', 'success')

            elif action == 'change_password':
                old_pw  = request.form.get('old_password', '').strip()
                new_pw  = request.form.get('new_password', '').strip()
                conf_pw = request.form.get('confirm_password', '').strip()

                user = query(db, "SELECT password FROM users WHERE id=%s", (uid,), one=True)
                if not user or not bcrypt.checkpw(old_pw.encode(), user['password'].encode()):
                    errors['old_password'] = 'Current password is incorrect.'
                if not new_pw or len(new_pw) < 6:
                    errors['new_password'] = 'New password must be at least 6 characters.'
                elif not re.search(r'[A-Za-z]', new_pw) or not re.search(r'\d', new_pw):
                    errors['new_password'] = 'Password must contain letters and numbers.'
                if new_pw != conf_pw:
                    errors['confirm_password'] = 'Passwords do not match.'

                if not errors:
                    hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
                    execute(db, "UPDATE users SET password=%s WHERE id=%s", (hashed, uid))
                    db.commit()
                    flash('Password changed successfully!', 'success')

        user = query(db, "SELECT * FROM users WHERE id=%s", (uid,), one=True)

        bookings = query(db, """
            SELECT b.ref_code, b.ticket_type, b.status, b.total_price, b.created_at,
                   b.seat_codes, b.payment_status,
                   m.title AS movie, c.name AS cinema, s.show_date, s.show_time
            FROM bookings b
            JOIN showings s ON s.id=b.showing_id
            JOIN movies   m ON m.id=s.movie_id
            JOIN cinemas  c ON c.id=s.cinema_id
            WHERE b.user_id=%s
            GROUP BY b.ref_code
            ORDER BY b.created_at DESC
            LIMIT 5
        """, (uid,))
        
        # Get movies for search functionality
        run_maintenance(db)
        all_movies = get_movies_with_status(db)
    finally:
        db.close()

    return render_template('profile.html',
        user_name=session.get('user_name'),
        user=user, is_admin=False,
        bookings=bookings, errors=errors, form=form, all_movies=all_movies)


# ─────────────────────────────────────────────────────────────
#  HELP & SUPPORT PAGE
# ─────────────────────────────────────────────────────────────
@app.route('/help')
def help_page():
    is_admin = session.get('is_admin', False)
    user_name = session.get('user_name') or session.get('admin_name', '')
    
    # Get movies for search functionality
    db = get_db()
    try:
        movies = get_movies_with_status(db)
    finally:
        db.close()
    
    return render_template('help.html', user_name=user_name, is_admin=is_admin, movies=movies)


@app.route('/settings')
@login_required
def settings():
    return redirect(url_for('profile'))

@app.route('/change-password')
@login_required
def change_password():
    return redirect(url_for('profile'))

@app.route('/notifications')
@login_required
def notifications():
    flash('Notifications coming soon.', 'info')
    return redirect(url_for('index'))

@app.route('/forgot-password')
def forgot_password():
    flash('Password reset: contact us at TICK.IT.ph or 0975-078-8092.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
