# Import libraries
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from helpers import login_required
from email.mime.text import MIMEText
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import psycopg
import psycopg.rows
from psycopg_pool import ConnectionPool
from io import BytesIO
import pandas as pd
import smtplib
import re
import os
import sys
import atexit
from contextlib import contextmanager
import threading

# Load environment variables from .env file
load_dotenv()

# Flush every print() line right away so logs show up immediately on Render
sys.stdout.reconfigure(line_buffering=True)

# Create a new Flask application instance
app = Flask(__name__)

# Render puts a proxy in front of the app: trust its X-Forwarded-For header so
# request.remote_addr (used by the login rate limit) is the real client IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Servers (Render) run in UTC; records must be stored in Colombia's local time
LOCAL_TZ = ZoneInfo("America/Bogota")

# Laboratories that can be selected in the public form
LABORATORIOS = [
    "Laboratorio de Competencias Digitales 1",
    "Laboratorio de Competencias Digitales 3",
    "Laboratorio de Redes",
]

# Load secret key from environment variable for persistent sessions across deploys
# Falls back to a random key only in development (sessions won't persist across restarts)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(24).hex())

# Protect all state-changing (POST) requests against CSRF
csrf = CSRFProtect(app)

# Harden the session cookie: never send it over plain HTTP in production (Render
# sets RENDER=true for every deployed service), and never let cross-site requests carry it.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))

# Limit login attempts and public form submissions per IP (brute-forcing and spam).
# In-memory storage is fine here because gunicorn runs a single worker.
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

# Pool of DB connections, reused across requests instead of opening/closing one each time.
_db_conninfo = psycopg.conninfo.make_conninfo(
    host=os.environ.get("DB_HOST"),
    port=os.environ.get("DB_PORT"),
    dbname=os.environ.get("DB_NAME"),
    user=os.environ.get("DB_USER"),
    password=os.environ.get("DB_PASSWORD"),
)
# prepare_threshold=None: Supabase's transaction pooler (port 6543) doesn't support prepared statements.
# check: discard connections the server closed while idle, before handing them out.
db_pool = ConnectionPool(
    conninfo=_db_conninfo, min_size=1, max_size=5, open=True,
    kwargs={"prepare_threshold": None},
    check=ConnectionPool.check_connection,
)
atexit.register(db_pool.close)


# Context manager to borrow a pooled connection and hand out a cursor
@contextmanager
def get_db_cursor(row_factory=None):
    """
    Borrows a connection from the pool, yields a cursor for use,
    commits the transaction if successful, rolls back on exception,
    and returns the connection to the pool for reuse.
    """
    with db_pool.connection() as conn:
        cursor = conn.cursor(row_factory=row_factory) if row_factory else conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


# Function to send emails asynchronously to avoid blocking HTTP requests
def send_email_async(subject, body, to_email):
    """
    Sends an email in a separate thread to prevent blocking the main application flow.
    Uses Gmail SMTP with App Password. Thread is non-daemon so it completes even on Render.
    """
    def send():
        try:
            email_user = os.environ.get("EMAIL_USER")
            email_pass = os.environ.get("EMAIL_PASS")
            if not email_user or not email_pass:
                print("Email credentials not configured.")
                return
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(email_user, email_pass)
            msg = MIMEText(body, "plain", "utf-8")
            msg["From"] = email_user
            msg["To"] = to_email
            msg["Subject"] = subject
            server.sendmail(email_user, to_email, msg.as_string())
            server.quit()
            print(f"Email sent successfully to {to_email}")
        except smtplib.SMTPAuthenticationError:
            print("Error: Gmail authentication failed. Check EMAIL_USER and EMAIL_PASS (App Password).")
        except smtplib.SMTPException as e:
            print(f"SMTP error sending email: {e}")
        except Exception as e:
            print(f"Unexpected error sending email: {e}")

    thread = threading.Thread(target=send, daemon=False)
    thread.start()


# Function to validate the fields of a laboratory report
def validate_report(form_data):
    """
    Returns an error message if any report field is invalid, otherwise None.
    Shared by the public form (add_data) and the admin edit form (update_data).
    """
    # Validate that all fields are filled
    if not all(form_data.values()):
        return "Todos los campos son obligatorios."
    # Validate teacher's name contains only letters and spaces
    if not re.match(r"^[A-Za-záéíóúÁÉÍÓÚñÑ\s]+$", form_data['nombre_docente']):
        return "El nombre del docente solo puede contener letras y espacios."
    # Validate institutional email format
    if not re.match(r"^[^,\s]+@(unicesmag\.edu\.co)$", form_data['correo_electronico']):
        return "Correo electrónico no válido. Debe ser un correo institucional."
    # Validate that exit time is after entry time
    if form_data['hora_ingreso'] >= form_data['hora_salida']:
        return "La hora de salida no debe ser anterior o igual a la hora de ingreso."
    return None


# Log out sessions whose user was deleted from the database
@app.before_request
def check_user_exists():
    if request.endpoint == 'static' or session.get("user_id") is None:
        return
    with get_db_cursor() as cursor:
        cursor.execute("SELECT 1 FROM users WHERE id = %s", (session["user_id"],))
        if cursor.fetchone() is None:
            session.clear()


# Make the laboratory list available to every template
@app.context_processor
def inject_laboratorios():
    return {"laboratorios": LABORATORIOS}


# Friendly page when a rate limit (login or public form) is exceeded
@app.errorhandler(429)
def ratelimit_handler(e):
    if request.endpoint == 'login_admin':
        return render_template("login.html", message=4), 429
    flash("Demasiados envíos seguidos. Espere un minuto e inténtelo de nuevo.", category='warning')
    return render_template('index.html', form_data=request.form), 429


# Route for the home page
@app.route("/")
def index():
    return render_template("index.html")


# Route to handle form submission for adding new data
@app.route("/add_data", methods=['POST'])
@limiter.limit("10 per minute")
def add_data():
    """
    Processes form data submitted via POST to add a new laboratory control record.
    Validates input fields, inserts data into the database, and provides user feedback.
    """
    form_data = {
        'identificador_laboratorio': request.form.get('identificador_laboratorio'),
        'nombre_docente': request.form.get('nombre_docente'),
        'correo_electronico': request.form.get('correo_electronico'),
        'programa': request.form.get('programa'),
        'hora_ingreso': request.form.get('hora_ingreso'),
        'hora_salida': request.form.get('hora_salida'),
        'observacion': request.form.get('observacion')
    }
    try:
        fecha_registro = datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')
        error = validate_report(form_data)
        # Only the laboratories listed in the form are accepted
        if not error and form_data['identificador_laboratorio'] not in LABORATORIOS:
            error = "Seleccione un laboratorio válido."
        if error:
            flash(error, category='warning')
            return render_template('index.html', form_data=form_data)

        with get_db_cursor() as cursor:
            query = """
            INSERT INTO control_laboratorios_sistemas
                (fecha_registro, identificador_laboratorio, nombre_docente,
                 correo_electronico, programa, hora_ingreso, hora_salida, observacion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (fecha_registro, *form_data.values()))

        flash("Incidente enviado exitosamente.", category='success')
        return redirect(url_for('index'))
    except Exception as e:
        print(f"Error saving incident report: {e}")
        flash("Ocurrió un error al guardar el incidente. Por favor, inténtelo de nuevo.", category='danger')
        return render_template('index.html', form_data=form_data)


# Route to handle admin login
@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login_admin():
    """
    Handles user login for admin panel.
    Validates credentials and manages user session.
    """
    session.clear()
    if request.method == "POST":
        input_username = request.form.get("username")
        input_password = request.form.get("password")
        if not input_username:
            return render_template("login.html", message=1)
        elif not input_password:
            return render_template("login.html", message=2)

        with get_db_cursor(row_factory=psycopg.rows.dict_row) as cursor:
            cursor.execute('SELECT * FROM users WHERE username = %s', (input_username,))
            user = cursor.fetchone()

        if user is None or not check_password_hash(user["hashed_password"], input_password):
            return render_template("login.html", message=3)

        session["user_id"] = user["id"]
        return redirect("/admin")
    return render_template("login.html")


# Route to display admin dashboard with all records
@app.route("/admin")
@login_required
def admin_data():
    with get_db_cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute('SELECT * FROM control_laboratorios_sistemas ORDER BY fecha_registro DESC')
        data = cursor.fetchall()
    return render_template("/admin.html", data=data)


# Route to get data for editing a specific record by ID
@app.route("/edit/<int:id>")
@login_required
def get_data(id):
    with get_db_cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute('SELECT * FROM control_laboratorios_sistemas WHERE id = %s', (id,))
        data = cursor.fetchone()
    if not data:
        flash("Registro no encontrado.", category='warning')
        return redirect(url_for('admin_data'))
    return render_template("edit.html", record=data)


# Helper function to normalize incidencia values for comparison
def normalize_incidencia(value):
    """
    Treat None, empty strings, and 'none' (case-insensitive) as equivalent ('').
    """
    if value is None:
        return ''
    value = str(value).strip()
    return '' if value.lower() == 'none' else value


# Route to update a record by ID
@app.route("/update/<int:id>", methods=['POST'])
@login_required
def update_data(id):
    """
    Updates a laboratory control record with form data, using the same validations as add_data.
    Sends notification email asynchronously if 'respuesta_incidencia' changes.
    """
    # respuesta_incidencia is optional (it can be left blank), the rest are required
    form_data = {
        'fecha_registro': request.form.get('fecha_registro'),
        'identificador_laboratorio': request.form.get('identificador_laboratorio'),
        'nombre_docente': request.form.get('nombre_docente'),
        'correo_electronico': request.form.get('correo_electronico'),
        'programa': request.form.get('programa'),
        'hora_ingreso': request.form.get('hora_ingreso'),
        'hora_salida': request.form.get('hora_salida'),
        'observacion': request.form.get('observacion')
    }
    respuesta_incidencia = request.form.get('respuesta_incidencia')

    error = validate_report(form_data)
    if not error:
        # Validate the registration date (e.g. 2025-03-14 10:30:00)
        try:
            datetime.fromisoformat(form_data['fecha_registro'])
        except ValueError:
            error = "Fecha de registro no válida. Use el formato AAAA-MM-DD HH:MM:SS."
    if error:
        flash(error, category='warning')
        return redirect(url_for('get_data', id=id))

    respuesta_incidencia_db = (
        respuesta_incidencia if respuesta_incidencia and str(respuesta_incidencia).strip() else None
    )

    try:
        with get_db_cursor() as cursor:
            # 1. Fetch previous response (also checks that the record exists)
            cursor.execute(
                "SELECT respuesta_incidencia FROM control_laboratorios_sistemas WHERE id = %s", (id,)
            )
            resultado = cursor.fetchone()
            if resultado is None:
                flash("Registro no encontrado.", category='warning')
                return redirect(url_for('admin_data'))
            respuesta_incidencia_anterior = resultado[0]

            # 2. Perform the UPDATE
            query = """
                UPDATE control_laboratorios_sistemas
                SET fecha_registro = %s,
                    identificador_laboratorio = %s,
                    nombre_docente = %s,
                    correo_electronico = %s,
                    programa = %s,
                    hora_ingreso = %s,
                    hora_salida = %s,
                    observacion = %s,
                    respuesta_incidencia = %s
                WHERE id = %s
            """
            cursor.execute(query, (*form_data.values(), respuesta_incidencia_db, id))
    except Exception as e:
        print(f"Error updating record {id}: {e}")
        flash("Ocurrió un error al guardar los cambios. Por favor, inténtelo de nuevo.", category='danger')
        return redirect(url_for('get_data', id=id))

    correo_electronico = form_data['correo_electronico']

    # 3. Send email only if there is an actual new/changed response (never for a cleared one,
    # which would otherwise email the instructor a literal "RESPUESTA: None").
    if respuesta_incidencia_db is not None and normalize_incidencia(respuesta_incidencia_anterior) != normalize_incidencia(respuesta_incidencia_db):
        subject = "Actualización: Respuesta a su reporte de incidencia - Laboratorios de Sistemas"
        body = f"""Paz y Bien,

Le informamos que se ha registrado una respuesta al incidente reportado en el Sistema de Control de Laboratorios:
        
----------------------------------------------------------------------
RESPUESTA:
{respuesta_incidencia_db}
----------------------------------------------------------------------

Si tiene alguna duda o requiere asistencia adicional, por favor responda a este mensaje.


Cordialmente,
Brayan Jair Enriquez
Infraestructura TI"""
        send_email_async(subject, body, correo_electronico)
        flash("Respuesta de incidencia enviada exitosamente.", category='success')
    else:
        flash('Datos actualizados exitosamente.', category='success')

    return redirect(url_for('admin_data'))


# Route to delete a record by ID
@app.route("/delete/<int:id>", methods=['POST'])
@login_required
def delete(id):
    with get_db_cursor() as cursor:
        cursor.execute('DELETE FROM control_laboratorios_sistemas WHERE id = %s', (id,))
    flash("Datos eliminados correctamente.", category='success')
    return redirect(url_for('admin_data'))


# Route to register a new user
@app.route("/register", methods=["GET", "POST"])
@login_required
def register():
    if request.method == "POST":
        input_username = request.form.get("username")
        input_password = request.form.get("password")
        input_confirmation = request.form.get("confirmation")
        if not input_username:
            return render_template("register.html", message=1)
        elif not input_password:
            return render_template("register.html", message=2)
        elif not input_confirmation:
            return render_template("register.html", message=4)
        elif input_password != input_confirmation:
            return render_template("register.html", message=3)
        elif len(input_password) < 8:
            return render_template("register.html", message=6)

        with get_db_cursor(row_factory=psycopg.rows.dict_row) as cursor:
            cursor.execute('SELECT * FROM users WHERE username = %s', (input_username,))
            if cursor.fetchone():
                return render_template("register.html", message=5)
            hash_password = generate_password_hash(input_password, method='pbkdf2:sha256', salt_length=8)
            cursor.execute(
                'INSERT INTO users (username, hashed_password) VALUES (%s, %s)',
                (input_username, hash_password)
            )

        # The admin stays logged in as themselves; the new account is for someone else
        flash(f"Usuario {input_username} registrado correctamente.", category='success')
        return redirect("/admin")
    return render_template("register.html")


# Route to list all users
@app.route("/users")
@login_required
def users():
    # Only id and username: password hashes never need to reach the template
    with get_db_cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute('SELECT id, username FROM users ORDER BY id')
        data = cursor.fetchall()
    return render_template("/users.html", data=data)


# Route to restore (reset) a user's password
@app.route("/restore/<int:id>", methods=['GET', 'POST'])
@login_required
def restore(id):
    with get_db_cursor() as cursor:
        cursor.execute("SELECT username FROM users WHERE id = %s", (id,))
        user = cursor.fetchone()
    if user is None:
        flash("Usuario no encontrado.", category='warning')
        return redirect("/users")
    username = user[0]

    if request.method == 'POST':
        new_password = request.form.get('password')
        confirmation = request.form.get('confirmation')

        if not new_password:
            return render_template("restore.html", message=2, id=id, username=username)
        elif not confirmation:
            return render_template("restore.html", message=4, id=id, username=username)
        elif new_password != confirmation:
            return render_template("restore.html", message=3, id=id, username=username)
        elif len(new_password) < 8:
            return render_template("restore.html", message=6, id=id, username=username)

        hash_password = generate_password_hash(new_password, method='pbkdf2:sha256', salt_length=8)
        with get_db_cursor() as cursor:
            cursor.execute("UPDATE users SET hashed_password = %s WHERE id = %s", (hash_password, id))

        flash("Datos actualizados exitosamente.", category='success')
        return redirect("/users")

    # GET: just show the form
    return render_template("restore.html", message=0, id=id, username=username)


# Route to delete a user by ID
@app.route("/deleteuser/<int:id>", methods=['POST'])
@login_required
def deleteuser(id):
    # Never let an admin delete their own account: this also guarantees the
    # last admin can't be deleted, which would lock everyone out of the panel
    if id == session.get("user_id"):
        flash("No puede eliminar su propio usuario.", category='warning')
        return redirect("/users")
    with get_db_cursor() as cursor:
        cursor.execute('DELETE FROM users WHERE id = %s', (id,))
    flash("Usuario eliminado correctamente.", category='success')
    return redirect("/users")


# Route to render reports page
@app.route("/reports", methods=['GET'])
@login_required
def reports():
    return render_template("/reports.html")


# Route to generate filtered data report
@app.route("/generate", methods=['GET', 'POST'])
@login_required
def generate():
    if request.method == 'POST':
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        if not start_date or not end_date:
            flash("Debe seleccionar una fecha de inicio y una fecha de fin.", category='warning')
            return render_template("/generate.html", data=[])

    with get_db_cursor(row_factory=psycopg.rows.dict_row) as cursor:
        if request.method == 'POST':
            start_end = start_date + " 00:00:00"
            end_date = end_date + " 23:59:59"
            session['start_date'] = start_end
            session['end_date'] = end_date
            cursor.execute(
                'SELECT * FROM control_laboratorios_sistemas WHERE fecha_registro BETWEEN %s AND %s'
                ' ORDER BY fecha_registro',
                (start_end, end_date)
            )
        else:
            cursor.execute('SELECT * FROM control_laboratorios_sistemas ORDER BY fecha_registro')
        data = cursor.fetchall()
    return render_template("/generate.html", data=data)


# Route to download report as Excel file
@app.route("/download")
@login_required
def download():
    start_date = session.get('start_date')
    end_date = session.get('end_date')
    # Columns listed explicitly so the Excel never depends on the table's column order
    query = """
        SELECT fecha_registro, identificador_laboratorio, nombre_docente, correo_electronico,
               programa, hora_ingreso, hora_salida, observacion, respuesta_incidencia
        FROM control_laboratorios_sistemas
    """
    with get_db_cursor() as cursor:
        if start_date and end_date:
            cursor.execute(query + " WHERE fecha_registro BETWEEN %s AND %s ORDER BY fecha_registro",
                           (start_date, end_date))
        else:
            cursor.execute(query + " ORDER BY fecha_registro")
        data = cursor.fetchall()

    columns = [
        'Fecha', 'Laboratorio', 'Nombres',
        'Correo electrónico', 'Programa',
        'Hora de ingreso', 'Hora de salida',
        'Observaciones', 'Respuesta'
    ]

    df = pd.DataFrame(data, columns=columns)
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce').dt.strftime('%Y-%m-%d')
    df['Hora de ingreso'] = df['Hora de ingreso'].astype(str)
    df['Hora de salida'] = df['Hora de salida'].astype(str)

    output = BytesIO()
    # strings_to_formulas=False: text typed in the public form (e.g. "=HYPERLINK(...)")
    # must reach the Excel as plain text, never as an active formula
    with pd.ExcelWriter(output, engine='xlsxwriter',
                        engine_kwargs={'options': {'strings_to_formulas': False}}) as writer:
        df.to_excel(writer, sheet_name='Reporte', index=False)
        workbook = writer.book
        worksheet = writer.sheets['Reporte']
        header_format = workbook.add_format({
            'bold': True,
            'font_size': 12,
            'bg_color': '#4F81BD',
            'font_color': 'white',
            'align': 'center',
            'valign': 'vcenter'
        })
        for col_num, value in enumerate(df.columns):
            worksheet.write(0, col_num, value, header_format)
        for idx, col in enumerate(df.columns):
            # Header length as the minimum, so an empty report still gets valid widths
            max_length = max([len(col), *df[col].astype(str).map(len)]) + 2
            worksheet.set_column(idx, idx, max_length)
    output.seek(0)
    timestamp = datetime.now(LOCAL_TZ).strftime('%Y%m%d_%H%M%S')
    filename = f'reporte_control_laboratorios_sistemas_{timestamp}.xlsx'
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


# Route to logout user and clear session
@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect("/")


# Start the Flask development server
if __name__ == "__main__":
    app.run(debug=False)