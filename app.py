from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import re

app = Flask(__name__)
app.secret_key = "parkverse-super-secret-key-change-this-in-production"
CORS(app, supports_credentials=True)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///parking.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_TIME_LIMIT'] = None  # tokens don't expire mid-session

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[])

RATE_PER_HOUR = 20  # flat rate in rupees, used for the pricing feature

ADMIN_EMAIL = "admin@parkverse.com"
ADMIN_PASSWORD = "admin123"


####################################
# DATABASE TABLES
####################################

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    mobile = db.Column(db.String(10), nullable=False)
    car_num = db.Column(db.String(20), nullable=False)
    vehicle_class = db.Column(db.String(20))
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ParkingSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slot_name = db.Column(db.String(20))
    status = db.Column(db.String(20), default="Available")
    booked_by = db.Column(db.String(100))
    near_elevator = db.Column(db.Boolean, default=False)


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(100))
    slot_id = db.Column(db.Integer, db.ForeignKey('parking_slot.id'))
    slot_name = db.Column(db.String(20))
    place = db.Column(db.String(100))
    booking_time = db.Column(db.String(50))
    duration_hours = db.Column(db.Float, default=1)
    end_time = db.Column(db.String(50))
    price = db.Column(db.Float, default=0)
    near_elevator_pref = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default="Active")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(100))
    channel = db.Column(db.String(10))  # "email" or "sms"
    message = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


####################################
# CREATE DATABASE + SEED DATA
####################################

@app.before_request
def create_tables():
    db.create_all()

    if ParkingSlot.query.count() == 0:
        # every alternate pair of slots is marked as "near elevator" for demo purposes
        elevator_ids = {1, 2, 5, 6, 9, 10, 13, 14, 17, 18}
        for i in range(1, 21):
            slot = ParkingSlot(
                slot_name=f"P-{i}",
                status="Available",
                near_elevator=(i in elevator_ids)
            )
            db.session.add(slot)
        db.session.commit()

    if not User.query.filter_by(email=ADMIN_EMAIL).first():
        admin = User(
            name="Admin",
            email=ADMIN_EMAIL,
            mobile="9999999999",
            car_num="ADMIN-000",
            vehicle_class="N/A",
            password=generate_password_hash(ADMIN_PASSWORD),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()


####################################
# VALIDATION HELPERS
####################################

def valid_mobile(mobile):
    return bool(mobile) and mobile.isdigit() and len(mobile) == 10


def valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def mask_email(email):
    try:
        name, domain = email.split("@")
        visible = name[:2]
        return f"{visible}{'*' * max(len(name) - 2, 1)}@{domain}"
    except Exception:
        return email


def mask_mobile(mobile):
    if not mobile or len(mobile) < 4:
        return mobile
    return f"{'*' * 6}{mobile[-4:]}"


def notify(user_email, mobile, message):
    """
    Mock notification gateway. In production this would call a real email/SMS
    provider (SendGrid, Twilio, etc). Here we log it and store it so the
    frontend can show a confirmation trail to the user.
    """
    email_note = Notification(
        user_email=user_email, channel="email",
        message=f"{message} (sent to {mask_email(user_email)})"
    )
    sms_note = Notification(
        user_email=user_email, channel="sms",
        message=f"{message} (sent to {mask_mobile(mobile)})"
    )
    db.session.add(email_note)
    db.session.add(sms_note)
    print(f"[MOCK EMAIL] -> {user_email}: {message}")
    print(f"[MOCK SMS]   -> {mobile}: {message}")


def admin_required():
    return session.get("is_admin") is True


####################################
# PAGE ROUTES
####################################

@app.route("/")
def home():
    return render_template("landing.html", logged_in=("user_id" in session))


@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("booking_page"))
    return render_template("login.html")


@app.route("/signup")
def signup_page():
    if "user_id" in session:
        return redirect(url_for("booking_page"))
    return render_template("signup.html")


@app.route("/booking")
def booking_page():
    if "user_id" not in session:
        return redirect(url_for("login_page"))
    return render_template(
        "booking.html",
        user_name=session.get("user_name"),
        user_email=session.get("user_email"),
        car_num=session.get("car_num"),
        rate_per_hour=RATE_PER_HOUR
    )


@app.route("/admin-login")
def admin_login_page():
    if admin_required():
        return redirect(url_for("admin_page"))
    return render_template("admin_login.html")


@app.route("/admin")
def admin_page():
    if not admin_required():
        return redirect(url_for("admin_login_page"))
    return render_template("admin.html", admin_name=session.get("user_name"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


####################################
# CSRF TOKEN ENDPOINT
####################################

@app.route("/api/csrf-token")
def csrf_token():
    return jsonify({"csrf_token": generate_csrf()})


####################################
# API: SIGNUP
####################################

@app.route("/api/signup", methods=["POST"])
@limiter.limit("8 per minute")
def api_signup():
    data = request.json or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    mobile = (data.get("mobile") or "").strip()
    car_num = (data.get("carNum") or "").strip().upper()
    vehicle_class = data.get("vehicleClass") or "Car"
    password = data.get("password") or ""

    if not name or len(name) < 2:
        return jsonify({"success": False, "message": "Please enter a valid name"})

    if not valid_email(email):
        return jsonify({"success": False, "message": "Please enter a valid email"})

    if not valid_mobile(mobile):
        return jsonify({"success": False, "message": "Mobile number must be exactly 10 digits"})

    if not car_num or len(car_num) < 4:
        return jsonify({"success": False, "message": "Please enter a valid car number"})

    if len(password) < 6:
        return jsonify({"success": False, "message": "Password must be at least 6 characters"})

    if User.query.filter_by(email=email).first():
        return jsonify({"success": False, "message": "Email already registered"})

    user = User(
        name=name,
        email=email,
        mobile=mobile,
        car_num=car_num,
        vehicle_class=vehicle_class,
        password=generate_password_hash(password)
    )

    db.session.add(user)
    db.session.commit()

    return jsonify({"success": True, "message": "Registration successful! Please login."})


####################################
# API: LOGIN
####################################

@app.route("/api/login", methods=["POST"])
@limiter.limit("10 per minute")
def api_login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email).first()

    if user and check_password_hash(user.password, password):
        session["user_id"] = user.id
        session["user_name"] = user.name
        session["user_email"] = user.email
        session["car_num"] = user.car_num
        session["is_admin"] = user.is_admin

        return jsonify({"success": True, "name": user.name, "redirect": url_for("booking_page")})

    return jsonify({"success": False, "message": "Invalid email or password"})


####################################
# API: ADMIN LOGIN
####################################

@app.route("/api/admin-login", methods=["POST"])
@limiter.limit("6 per minute")
def api_admin_login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email, is_admin=True).first()

    if user and check_password_hash(user.password, password):
        session["user_id"] = user.id
        session["user_name"] = user.name
        session["user_email"] = user.email
        session["car_num"] = user.car_num
        session["is_admin"] = True
        return jsonify({"success": True, "redirect": url_for("admin_page")})

    return jsonify({"success": False, "message": "Invalid admin credentials"})


####################################
# API: GET PARKING SLOTS
####################################

@app.route("/api/slots")
def api_slots():
    near_elevator = request.args.get("near_elevator")

    query = ParkingSlot.query
    if near_elevator == "true":
        query = query.filter_by(near_elevator=True)

    all_slots = query.order_by(ParkingSlot.id).all()

    result = []
    for slot in all_slots:
        result.append({
            "id": slot.id,
            "slot": slot.slot_name,
            "status": slot.status,
            "booked_by": slot.booked_by,
            "near_elevator": slot.near_elevator
        })

    return jsonify(result)


####################################
# API: BOOK SLOT (place, time, duration, elevator preference)
####################################

@app.route("/api/book", methods=["POST"])
def api_book():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Please login first"})

    data = request.json or {}
    slot_id = data.get("slot_id")
    place = (data.get("place") or "").strip()
    booking_time = (data.get("time") or "").strip()
    near_elevator_pref = bool(data.get("near_elevator"))

    try:
        duration_hours = float(data.get("duration", 1))
    except (TypeError, ValueError):
        duration_hours = 1

    duration_hours = max(1, min(duration_hours, 24))

    if not slot_id:
        return jsonify({"success": False, "message": "Please select a slot"})

    if not place:
        return jsonify({"success": False, "message": "Please select a place / zone"})

    if not booking_time:
        return jsonify({"success": False, "message": "Please choose a date & time"})

    slot = ParkingSlot.query.get(slot_id)

    if not slot:
        return jsonify({"success": False, "message": "Slot not found"})

    if slot.status == "Booked":
        return jsonify({"success": False, "message": "This slot is already booked"})

    price = round(duration_hours * RATE_PER_HOUR, 2)

    try:
        start_dt = datetime.fromisoformat(booking_time)
        end_dt = start_dt + timedelta(hours=duration_hours)
        end_time_str = end_dt.isoformat()
    except ValueError:
        end_time_str = ""

    slot.status = "Booked"
    slot.booked_by = session["user_email"]

    booking = Booking(
        user_email=session["user_email"],
        slot_id=slot.id,
        slot_name=slot.slot_name,
        place=place,
        booking_time=booking_time,
        duration_hours=duration_hours,
        end_time=end_time_str,
        price=price,
        near_elevator_pref=near_elevator_pref,
        status="Active"
    )

    db.session.add(booking)

    user = User.query.filter_by(email=session["user_email"]).first()
    notify(
        session["user_email"],
        user.mobile if user else "",
        f"Booking confirmed for slot {slot.slot_name} at {place}, {duration_hours}h (Rs.{price})"
    )

    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Slot {slot.slot_name} booked successfully! Confirmation sent to your email & mobile.",
        "slot_name": slot.slot_name,
        "price": price,
        "end_time": end_time_str
    })


####################################
# API: MY BOOKINGS
####################################

@app.route("/api/my-bookings")
def api_my_bookings():
    if "user_id" not in session:
        return jsonify([])

    bookings = Booking.query.filter_by(
        user_email=session["user_email"], status="Active"
    ).order_by(Booking.created_at.desc()).all()

    result = []
    for b in bookings:
        result.append({
            "id": b.id,
            "slot_id": b.slot_id,
            "slot_name": b.slot_name,
            "place": b.place,
            "time": b.booking_time,
            "duration": b.duration_hours,
            "end_time": b.end_time,
            "price": b.price,
            "near_elevator": b.near_elevator_pref
        })

    return jsonify(result)


####################################
# API: NOTIFICATIONS (mock email/SMS trail)
####################################

@app.route("/api/notifications")
def api_notifications():
    if "user_id" not in session:
        return jsonify([])

    notes = Notification.query.filter_by(
        user_email=session["user_email"]
    ).order_by(Notification.created_at.desc()).limit(20).all()

    return jsonify([{
        "id": n.id,
        "channel": n.channel,
        "message": n.message,
        "time": n.created_at.strftime("%d %b, %I:%M %p")
    } for n in notes])


####################################
# API: DASHBOARD STATS
####################################

@app.route("/api/dashboard")
def api_dashboard():
    total = ParkingSlot.query.count()
    booked = ParkingSlot.query.filter_by(status="Booked").count()
    available = total - booked
    elevator_free = ParkingSlot.query.filter_by(near_elevator=True, status="Available").count()

    return jsonify({
        "total_slots": total,
        "booked_slots": booked,
        "available_slots": available,
        "occupancy": round(booked * 100 / total, 2) if total else 0,
        "elevator_free": elevator_free
    })


####################################
# API: CANCEL BOOKING
####################################

@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Please login first"})

    data = request.json or {}
    slot_id = data.get("slot_id")

    slot = ParkingSlot.query.get(slot_id)
    if not slot:
        return jsonify({"success": False, "message": "Slot not found"})

    slot.status = "Available"
    slot.booked_by = ""

    booking = Booking.query.filter_by(
        slot_id=slot_id, user_email=session["user_email"], status="Active"
    ).first()

    if booking:
        booking.status = "Cancelled"
        user = User.query.filter_by(email=session["user_email"]).first()
        notify(
            session["user_email"],
            user.mobile if user else "",
            f"Booking for slot {slot.slot_name} has been cancelled"
        )

    db.session.commit()

    return jsonify({"success": True, "message": "Booking cancelled"})


####################################
# ====== ADMIN API ======
####################################

def require_admin_json():
    if not admin_required():
        return jsonify({"success": False, "message": "Admin access required"}), 403
    return None


@app.route("/api/admin/overview")
def admin_overview():
    guard = require_admin_json()
    if guard:
        return guard

    slots = ParkingSlot.query.order_by(ParkingSlot.id).all()
    bookings = Booking.query.filter_by(status="Active").order_by(Booking.created_at.desc()).all()
    users = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all()

    total = len(slots)
    booked = sum(1 for s in slots if s.status == "Booked")
    revenue = round(sum(b.price or 0 for b in bookings), 2)

    return jsonify({
        "stats": {
            "total_slots": total,
            "booked_slots": booked,
            "available_slots": total - booked,
            "total_users": len(users),
            "active_bookings": len(bookings),
            "revenue": revenue
        },
        "slots": [{
            "id": s.id, "slot": s.slot_name, "status": s.status,
            "booked_by": s.booked_by, "near_elevator": s.near_elevator
        } for s in slots],
        "bookings": [{
            "id": b.id, "user_email": b.user_email, "slot_name": b.slot_name,
            "place": b.place, "time": b.booking_time, "duration": b.duration_hours,
            "price": b.price, "near_elevator": b.near_elevator_pref
        } for b in bookings],
        "users": [{
            "id": u.id, "name": u.name, "email": u.email, "mobile": u.mobile,
            "car_num": u.car_num, "vehicle_class": u.vehicle_class
        } for u in users]
    })


@app.route("/api/admin/slot/add", methods=["POST"])
def admin_add_slot():
    guard = require_admin_json()
    if guard:
        return guard

    data = request.json or {}
    slot_name = (data.get("slot_name") or "").strip()
    near_elevator = bool(data.get("near_elevator"))

    if not slot_name:
        return jsonify({"success": False, "message": "Slot name is required"})

    if ParkingSlot.query.filter_by(slot_name=slot_name).first():
        return jsonify({"success": False, "message": "A slot with this name already exists"})

    slot = ParkingSlot(slot_name=slot_name, status="Available", near_elevator=near_elevator)
    db.session.add(slot)
    db.session.commit()

    return jsonify({"success": True, "message": f"Slot {slot_name} added"})


@app.route("/api/admin/slot/delete", methods=["POST"])
def admin_delete_slot():
    guard = require_admin_json()
    if guard:
        return guard

    data = request.json or {}
    slot = ParkingSlot.query.get(data.get("slot_id"))

    if not slot:
        return jsonify({"success": False, "message": "Slot not found"})

    Booking.query.filter_by(slot_id=slot.id, status="Active").update({"status": "Cancelled"})
    db.session.delete(slot)
    db.session.commit()

    return jsonify({"success": True, "message": "Slot removed"})


@app.route("/api/admin/slot/toggle-elevator", methods=["POST"])
def admin_toggle_elevator():
    guard = require_admin_json()
    if guard:
        return guard

    data = request.json or {}
    slot = ParkingSlot.query.get(data.get("slot_id"))

    if not slot:
        return jsonify({"success": False, "message": "Slot not found"})

    slot.near_elevator = not slot.near_elevator
    db.session.commit()

    return jsonify({"success": True, "near_elevator": slot.near_elevator})


@app.route("/api/admin/booking/cancel", methods=["POST"])
def admin_cancel_booking():
    guard = require_admin_json()
    if guard:
        return guard

    data = request.json or {}
    booking = Booking.query.get(data.get("booking_id"))

    if not booking or booking.status != "Active":
        return jsonify({"success": False, "message": "Booking not found"})

    slot = ParkingSlot.query.get(booking.slot_id)
    if slot:
        slot.status = "Available"
        slot.booked_by = ""

    booking.status = "Cancelled"
    db.session.commit()

    return jsonify({"success": True, "message": "Booking cancelled by admin"})


####################################
# RUN SERVER
####################################

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
