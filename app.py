import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
# Secure secret key for flash messages
app.secret_key = 'shopperia_secret_key'

# Enable CORS for React frontend (allow all origins for now)
CORS(app)

# Database Configuration
# Use Postgres if DATABASE_URL is set (from Render/Supabase), otherwise fallback to local SQLite
database_url = os.environ.get('DATABASE_URL', 'sqlite:///shopperia.db')
# SQLAlchemy 1.4+ URL connection issue workaround for Postgres
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- MODELS ---

class Produk(db.Model):
    __tablename__ = 'produk'
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(200), nullable=False)
    harga = db.Column(db.Float, nullable=False)
    stok = db.Column(db.Integer, nullable=False)

class Order(db.Model):
    __tablename__ = 'order'
    id = db.Column(db.Integer, primary_key=True)
    produk_id = db.Column(db.Integer, db.ForeignKey('produk.id'), nullable=False)
    jumlah = db.Column(db.Integer, nullable=False)
    total_harga = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), nullable=False, default='Shipping') # Shipping, RTS, Delivered
    
    # Extra fields for CRM
    customer_name = db.Column(db.String(100), nullable=True)
    customer_phone = db.Column(db.String(20), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    payment_method = db.Column(db.String(20), nullable=True, default='COD')
    courier_name = db.Column(db.String(50), nullable=True)
    courier_awb = db.Column(db.String(50), nullable=True)
    kurir_phone = db.Column(db.String(20), nullable=True)
    cs_token = db.Column(db.String(50), nullable=True, default='Admin')
    monitoring_category = db.Column(db.String(50), default='Aman')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    produk = db.relationship('Produk', backref=db.backref('orders', lazy=True))

    def to_dict(self):
        # Format required by the React Frontend
        return {
            'id': f"ORD-{self.id:04d}",
            'db_id': self.id,
            'date': self.created_at.isoformat() if self.created_at else datetime.utcnow().isoformat(),
            'customer': {
                'name': self.customer_name or 'Unknown',
                'phone': self.customer_phone or '-'
            },
            'address': self.address or 'Unknown',
            'product': self.produk.nama if self.produk else 'Unknown Product',
            'csToken': self.cs_token or 'Admin',
            'courierInfo': {
                'name': self.courier_name or 'Unknown',
                'awb': self.courier_awb or '-',
                'kurirPhone': self.kurir_phone or ''
            },
            'paymentMethod': self.payment_method or 'COD',
            'tracking': {
                'statusCategory': self.monitoring_category or 'Aman',
                'orderStatus': self.status or 'Shipping',
                'statusText': 'Synced with Live DB',
                'lastUpdate': self.created_at.isoformat() if self.created_at else datetime.utcnow().isoformat()
            }
        }

class Admin(db.Model):
    __tablename__ = 'admin'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), default='superadmin')
    permissions = db.Column(db.Text, default='dashboard,terkendala,orders,templates,import_history,ranking,usermanagement')
    
    def to_dict(self):
        return {
            'id': f"usr_{self.id}",
            'name': self.username,
            'email': f"{self.username}@shopperia.com",
            'phone': self.username,
            'role': self.role,
            'permissions': self.permissions.split(',') if self.permissions else []
        }

def init_db():
    with app.app_context():
        db.create_all()
        # Seed default admin user if none exists
        admin = Admin.query.filter_by(username='admin').first()
        if not admin:
            default_hash = generate_password_hash('admin123')
            new_admin = Admin(username='admin', password_hash=default_hash, role='superadmin')
            db.session.add(new_admin)
            db.session.commit()
            
        # Seed dummy product if none exists to prevent foreign key errors for early testing
        if not Produk.query.first():
            dummy_prod = Produk(nama='Sampo uban CNL • Beli 1 Rp 125 Ribu - Hitam Alami x1', harga=125000, stok=100)
            db.session.add(dummy_prod)
            db.session.commit()

init_db()

# --- JSON API ENDPOINTS FOR REACT FRONTEND ---

@app.route('/api/orders', methods=['GET'])
def get_orders():
    """Retrieve all orders formatted for the React frontend"""
    orders = Order.query.order_by(Order.id.desc()).all()
    return jsonify([order.to_dict() for order in orders])

@app.route('/api/orders', methods=['POST'])
def api_create_order():
    """Create a new order from API / Webhook Simulated / Import"""
    try:
        data = request.json
        if not data:
            # Maybe it's a list for Excel Import
            if isinstance(request.json, list):
                orders_data = request.json
                created_orders = []
                produk = Produk.query.first()
                if not produk:
                    return jsonify({"error": "No products available in database"}), 404
                
                for item in orders_data:
                    new_order = Order(
                        produk_id=produk.id,
                        jumlah=1,
                        total_harga=produk.harga,
                        status=item.get('tracking', {}).get('orderStatus', 'Shipping'),
                        customer_name=item.get('customer', {}).get('name', 'Imported Row'),
                        customer_phone=item.get('customer', {}).get('phone', '-'),
                        address=item.get('address', 'API Address'),
                        payment_method=item.get('paymentMethod', 'COD'),
                        courier_name=item.get('courierInfo', {}).get('name', 'Unknown'),
                        courier_awb=item.get('courierInfo', {}).get('awb', '-'),
                        cs_token=item.get('csToken', 'Admin'),
                        monitoring_category=item.get('tracking', {}).get('statusCategory', 'Aman')
                    )
                    db.session.add(new_order)
                    db.session.flush() # To get ID for response
                    created_orders.append(new_order.to_dict())
                
                db.session.commit()
                return jsonify({"success": True, "count": len(created_orders), "orders": created_orders}), 201

        # Single creation
        produk = Produk.query.first()
        if not produk:
            return jsonify({"error": "No products available in database"}), 404
            
        new_order = Order(
            produk_id=produk.id,
            jumlah=1,
            total_harga=produk.harga,
            status=data.get('tracking', {}).get('orderStatus', 'Shipping'),
            customer_name=data.get('customer', {}).get('name', 'API User'),
            customer_phone=data.get('customer', {}).get('phone', '-'),
            address=data.get('address', 'API Address'),
            payment_method=data.get('paymentMethod', 'COD'),
            courier_name=data.get('courierInfo', {}).get('name', 'Unknown'),
            courier_awb=data.get('courierInfo', {}).get('awb', '-'),
            cs_token=data.get('csToken', 'Admin'),
            monitoring_category=data.get('tracking', {}).get('statusCategory', 'Aman')
        )
        db.session.add(new_order)
        db.session.commit()
        return jsonify(new_order.to_dict()), 201
    except Exception as e:
        print("Error details:", str(e))
        return jsonify({"error": str(e)}), 500

@app.route('/api/orders/<int:order_id>', methods=['PATCH'])
def api_update_order(order_id):
    """Update order details (Status, Monitoring, Courier Phone)"""
    order = Order.query.get_or_404(order_id)
    data = request.json
    
    if not data:
        return jsonify({"error": "No JSON payload provided"}), 400
        
    if 'statusCategory' in data:
        order.monitoring_category = data['statusCategory']
    if 'orderStatus' in data:
        order.status = data['orderStatus']
    if 'kurirPhone' in data:
        order.kurir_phone = data['kurirPhone']
        
    db.session.commit()
    return jsonify(order.to_dict()), 200

@app.route('/api/users', methods=['GET'])
def get_users():
    """Retrieve all users"""
    users = Admin.query.all()
    return jsonify([user.to_dict() for user in users])

@app.route('/api/login', methods=['POST'])
def api_login():
    """Authenticate user from React frontend via JSON"""
    data = request.json
    identifier = data.get('identifier')
    password = data.get('password')
    
    user = Admin.query.filter_by(username=identifier).first()
    if user and check_password_hash(user.password_hash, password):
        return jsonify({"success": True, "user": user.to_dict()}), 200
    
    return jsonify({"success": False, "error": "Invalid credentials"}), 401

# --- EXISTING HTML ROUTES (Kept for backwards compatibility) ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Harap login terlebih dahulu untuk mengakses halaman ini.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """Halaman utama dengan data produk dan order (HTML)"""
    produk_list = Produk.query.order_by(Produk.id.desc()).all()
    order_list = Order.query.order_by(Order.id.desc()).all()
    
    orders_data = []
    for order in order_list:
        orders_data.append({
            'id': order.id,
            'nama_produk': order.produk.nama if order.produk else 'Unknown',
            'jumlah': order.jumlah,
            'total_harga': order.total_harga,
            'status': order.status
        })

    return render_template('index.html', produk_list=produk_list, order_list=orders_data)

@app.route('/tambah_produk', methods=('POST',))
@login_required
def tambah_produk():
    nama = request.form.get('nama')
    harga = request.form.get('harga')
    stok = request.form.get('stok')

    if not nama or not harga or not stok:
        flash('Semua field produk harus diisi!')
    else:
        new_produk = Produk(nama=nama, harga=float(harga), stok=int(stok))
        db.session.add(new_produk)
        db.session.commit()
        flash('Produk berhasil ditambahkan!')
    return redirect(url_for('index'))

@app.route('/buat_order', methods=('POST',))
def buat_order():
    produk_id = request.form.get('produk_id')
    jumlah = request.form.get('jumlah')
    
    if not produk_id or not jumlah:
        flash('Silakan pilih produk dan isi jumlah!')
        return redirect(url_for('index'))
        
    jumlah = int(jumlah)
    produk = Produk.query.get(produk_id)
    
    if produk:
        if produk.stok >= jumlah and jumlah > 0:
            total_harga = produk.harga * jumlah
            produk.stok = produk.stok - jumlah
            
            new_order = Order(produk_id=produk.id, jumlah=jumlah, total_harga=total_harga, status='Pending')
            db.session.add(new_order)
            db.session.commit()
            flash('Order berhasil dibuat!')
        else:
            flash('Gagal membuat order: Stok tidak mencukupi atau jumlah tidak valid!')
    else:
        flash('Produk tidak ditemukan!')
        
    return redirect(url_for('index'))

@app.route('/update_status/<int:order_id>', methods=('POST',))
@login_required
def update_status(order_id):
    status_baru = request.form.get('status')
    if status_baru:
        order = Order.query.get(order_id)
        if order:
            order.status = status_baru
            db.session.commit()
            flash(f'Status order #{order_id} berhasil diupdate menjadi {status_baru}!')
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login_html():
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        admin = Admin.query.filter_by(username=username).first()

        if admin and check_password_hash(admin.password_hash, password):
            session['admin_id'] = admin.id
            session['username'] = admin.username
            flash('Login berhasil!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Username atau password salah!', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Anda telah logout.', 'info')
    return redirect(url_for('login_html'))

@app.route('/admin')
@login_required
def admin_dashboard():
    produk_list = Produk.query.order_by(Produk.id.desc()).all()
    order_list = Order.query.order_by(Order.id.desc()).all()
    
    orders_data = []
    for order in order_list:
        orders_data.append({
            'id': order.id,
            'nama_produk': order.produk.nama if order.produk else 'Unknown',
            'jumlah': order.jumlah,
            'total_harga': order.total_harga,
            'status': order.status
        })
        
    return render_template('admin.html', produk_list=produk_list, order_list=orders_data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
