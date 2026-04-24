# ============================================================
#  TICK.IT QR CODE SYSTEM
#  QR Generation, Validation & Verification
# ============================================================

import qrcode
import qrcode.image.pil
import json
import hmac
import hashlib
import base64
from io import BytesIO
from datetime import datetime, timedelta
from PIL import Image
import os

# QR Code Secret (move to environment variable in production)
QR_SECRET_KEY = os.getenv('QR_SECRET_KEY', 'your-secret-key-change-in-production')

# QR Code Version Configuration
QR_CONFIG = {
    'version': 1,
    'box_size': 10,
    'border': 4,
    'fill_color': '#000000',
    'back_color': '#FFFFFF'
}


def generate_qr_data(booking_id, ref_code, movie_title, showtime, seats, customer_name, 
                     booking_type='online', expiry_hours=5):
    """
    Generate signed QR code data structure.
    
    Args:
        booking_id: Internal booking ID
        ref_code: Human-readable reference code
        movie_title: Movie name
        showtime: Show datetime string
        seats: List of seat codes
        customer_name: Customer name
        booking_type: 'online' or 'walkin'
        expiry_hours: Hours until QR expires
    
    Returns:
        dict: Signed QR data structure
    """
    now = datetime.now()
    expiry = now + timedelta(hours=expiry_hours)
    
    data = {
        'v': '1.0',                    # Version
        'bid': str(booking_id),        # Booking ID
        'ref': ref_code,               # Reference code
        'm': movie_title[:50],         # Movie title (truncated)
        's': showtime,                 # Show time
        'seats': seats,                # Seat codes
        'c': customer_name[:30],      # Customer name
        't': int(now.timestamp()),     # Created timestamp
        'exp': int(expiry.timestamp()), # Expiry timestamp
        'type': booking_type           # Booking type
    }
    
    # Generate HMAC signature
    signature = generate_hmac_signature(data)
    data['h'] = signature
    
    return data


def generate_hmac_signature(data):
    """
    Generate HMAC-SHA256 signature for QR data.
    
    Args:
        data: QR data dictionary (without 'h' key)
    
    Returns:
        str: Base64-encoded signature
    """
    # Create canonical JSON string (sorted keys, no spaces)
    canonical = json.dumps(data, sort_keys=True, separators=(',', ':'))
    
    # Generate HMAC
    signature = hmac.new(
        QR_SECRET_KEY.encode('utf-8'),
        canonical.encode('utf-8'),
        hashlib.sha256
    ).digest()
    
    # Return base64-encoded signature (first 16 chars for QR compactness)
    return base64.urlsafe_b64encode(signature)[:16].decode('utf-8')


def verify_qr_signature(data):
    """
    Verify QR code data signature.
    
    Args:
        data: QR data dictionary with 'h' key
    
    Returns:
        bool: True if signature valid
    """
    if 'h' not in data:
        return False
    
    stored_signature = data.pop('h')
    expected_signature = generate_hmac_signature(data)
    
    # Restore signature for logging
    data['h'] = stored_signature
    
    return hmac.compare_digest(stored_signature, expected_signature)


def generate_qr_image(qr_data, size=400):
    """
    Generate QR code image from data.
    
    Args:
        qr_data: QR data dictionary
        size: Output image size in pixels
    
    Returns:
        PIL.Image: QR code image
    """
    qr = qrcode.QRCode(
        version=QR_CONFIG['version'],
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=QR_CONFIG['box_size'],
        border=QR_CONFIG['border']
    )
    
    # Add data as compact JSON
    qr.add_data(json.dumps(qr_data, separators=(',', ':')))
    qr.make(fit=True)
    
    # Generate image
    img = qr.make_image(
        fill_color=QR_CONFIG['fill_color'],
        back_color=QR_CONFIG['back_color']
    )
    
    # Resize to requested size
    img = img.resize((size, size), Image.LANCZOS)
    
    return img


def generate_qr_with_logo(qr_data, logo_path=None, size=400):
    """
    Generate QR code with optional center logo.
    
    Args:
        qr_data: QR data dictionary
        logo_path: Path to logo image (optional)
        size: Output image size
    
    Returns:
        PIL.Image: QR code image with logo
    """
    qr_img = generate_qr_image(qr_data, size)
    
    if logo_path and os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path)
            
            # Calculate logo size (20% of QR)
            logo_size = int(size * 0.2)
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
            
            # Calculate position (center)
            pos = ((size - logo_size) // 2, (size - logo_size) // 2)
            
            # Paste logo
            qr_img.paste(logo, pos, logo if logo.mode == 'RGBA' else None)
        except Exception as e:
            print(f"Error adding logo: {e}")
    
    return qr_img


def save_qr_image(img, output_path, format='PNG'):
    """
    Save QR image to file.
    
    Args:
        img: PIL.Image object
        output_path: File path to save
        format: Image format
    
    Returns:
        str: Saved file path
    """
    img.save(output_path, format=format)
    return output_path


def get_qr_image_bytes(img, format='PNG'):
    """
    Get QR image as bytes for streaming.
    
    Args:
        img: PIL.Image object
        format: Image format
    
    Returns:
        bytes: Image data
    """
    buffer = BytesIO()
    img.save(buffer, format=format)
    buffer.seek(0)
    return buffer.getvalue()


def decode_qr_data(qr_string):
    """
    Decode QR code string to dictionary.
    
    Args:
        qr_string: JSON string from scanned QR
    
    Returns:
        dict: Decoded data or None if invalid
    """
    try:
        data = json.loads(qr_string)
        return data
    except json.JSONDecodeError:
        return None


def validate_qr_code(qr_data, db_connection=None):
    """
    Comprehensive QR code validation.
    
    Args:
        qr_data: Decoded QR data
        db_connection: Optional DB connection for full validation
    
    Returns:
        dict: Validation result with status and details
    """
    result = {
        'valid': False,
        'status': 'invalid',
        'message': '',
        'booking_id': None,
        'booking_data': None
    }
    
    # 1. Check required fields
    required = ['v', 'bid', 'ref', 'h', 'exp']
    missing = [f for f in required if f not in qr_data]
    if missing:
        result['message'] = f"Missing fields: {', '.join(missing)}"
        return result
    
    # 2. Verify signature
    if not verify_qr_signature(qr_data.copy()):
        result['message'] = "Invalid QR code signature"
        return result
    
    # 3. Check expiry
    now = int(datetime.now().timestamp())
    if now > qr_data['exp']:
        result['status'] = 'expired'
        result['message'] = "QR code has expired"
        return result
    
    # 4. Basic structure valid
    result['valid'] = True
    result['status'] = 'valid_structure'
    result['booking_id'] = qr_data['bid']
    result['booking_data'] = qr_data
    
    # 5. Database validation (if connection provided)
    if db_connection:
        from app import query  # Import here to avoid circular dependency
        
        booking = query(db_connection, 
            "SELECT * FROM bookings WHERE id=%s AND ref_code=%s",
            (qr_data['bid'], qr_data['ref']), one=True)
        
        if not booking:
            result['valid'] = False
            result['status'] = 'not_found'
            result['message'] = "Booking not found"
            return result
        
        # Check if already checked in
        if booking['status'] == 'checked_in':
            result['valid'] = False
            result['status'] = 'already_used'
            result['message'] = "Booking already checked in"
            result['booking_data'] = booking
            return result
        
        # Check if cancelled/expired
        if booking['status'] in ['cancelled', 'expired']:
            result['valid'] = False
            result['status'] = booking['status']
            result['message'] = f"Booking is {booking['status']}"
            return result
        
        # Full validation passed
        result['status'] = 'valid'
        result['message'] = "Booking verified"
        result['booking_data'] = booking
    
    return result


def generate_booking_qr_code(booking_id, db):
    """
    Generate and save QR code for a booking.
    
    Args:
        booking_id: Booking ID
        db: Database connection
    
    Returns:
        dict: QR data and file path
    """
    from app import query
    
    # Get booking details
    booking = query(db, """
        SELECT b.id, b.ref_code, b.booking_type, b.status,
               m.title as movie_title,
               CONCAT(s.show_date, ' ', s.show_time) as showtime,
               b.seat_codes, b.customer_name
        FROM bookings b
        JOIN showings s ON b.showing_id = s.id
        JOIN movies m ON s.movie_id = m.id
        WHERE b.id = %s
    """, (booking_id,), one=True)
    
    if not booking:
        return None
    
    # Parse seats
    seats = booking['seat_codes'].split(',') if booking['seat_codes'] else []
    
    # Generate QR data
    expiry_hours = 5 if booking['booking_type'] == 'walkin' else 24
    qr_data = generate_qr_data(
        booking_id=booking['id'],
        ref_code=booking['ref_code'],
        movie_title=booking['movie_title'],
        showtime=booking['showtime'],
        seats=seats,
        customer_name=booking['customer_name'],
        booking_type=booking['booking_type'],
        expiry_hours=expiry_hours
    )
    
    # Generate image
    img = generate_qr_image(qr_data, size=400)
    
    # Save to file
    qr_filename = f"qr_{booking['ref_code']}.png"
    qr_path = os.path.join('static', 'qr_codes', qr_filename)
    os.makedirs(os.path.dirname(qr_path), exist_ok=True)
    save_qr_image(img, qr_path)
    
    # Update database
    from app import execute
    execute(db, """
        UPDATE bookings 
        SET qr_code_data = %s, qr_image_path = %s
        WHERE id = %s
    """, (json.dumps(qr_data), qr_path, booking_id))
    
    return {
        'data': qr_data,
        'image_path': qr_path,
        'image_url': f'/static/qr_codes/{qr_filename}'
    }


# Export key functions
__all__ = [
    'generate_qr_data',
    'generate_qr_image',
    'generate_booking_qr_code',
    'validate_qr_code',
    'decode_qr_data',
    'verify_qr_signature'
]
