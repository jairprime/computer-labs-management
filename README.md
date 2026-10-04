# Control de Laboratorios de Sistemas

Aplicación web para reportar y dar seguimiento a incidentes en los laboratorios de sistemas de la Universidad CESMAG, construida con **Flask 2.3 + Jinja2** y **PostgreSQL** (vía `psycopg` 3, sin ORM). Cualquier docente puede reportar un incidente desde un formulario público, y el personal de Infraestructura TI lo gestiona, responde por correo y exporta reportes en Excel desde un panel protegido con login.

## Stack

| Capa          | Tecnología                                                 |
| ------------- | ---------------------------------------------------------- |
| Backend       | Python 3.12, Flask 2.3, Flask-WTF (CSRF), Flask-Limiter    |
| Base de datos | PostgreSQL, psycopg 3 + psycopg-pool                       |
| Reportes      | pandas, XlsxWriter                                         |
| Correo        | Gmail SMTP (`smtplib`) con contraseña de aplicación        |
| UI            | Jinja2, Bootstrap 4 (Bootswatch Materia, CDN)              |
| Despliegue    | Gunicorn, Render                                           |

## Características

- **Formulario público** de incidentes: fecha, laboratorio, docente, correo institucional (`@unicesmag.edu.co`), programa, horario y observación.
- **Panel de administración** (requiere login): listar, editar, responder y eliminar reportes.
- **Notificación por correo**: al guardar una respuesta, se envía automáticamente un email al docente que reportó.
- **Gestión de administradores**: crear usuarios, restablecer contraseñas y eliminarlos.
- **Reportes en Excel**: filtra incidentes por rango de fechas y descárgalos como `.xlsx`.
- **Seguridad**: protección CSRF, contraseñas con hash `pbkdf2:sha256`, límite de 10 intentos de login por minuto por IP y cookies de sesión `Secure` en producción.

## Estructura

```
computer-labs-management/
├── app.py                 # Rutas y lógica de la aplicación
├── helpers.py             # Decorador login_required
├── templates/             # Vistas Jinja2
│   ├── layout.html        # Plantilla base
│   ├── index.html         # Formulario público de incidentes
│   ├── login.html
│   ├── admin.html         # Listado de incidentes
│   ├── edit.html          # Editar / responder incidente
│   ├── users.html         # Gestión de administradores
│   ├── register.html
│   ├── restore.html       # Restablecer contraseña
│   ├── reports.html
│   └── generate.html      # Filtro por fechas y descarga
├── static/
│   ├── css/
│   ├── js/
│   └── img/
├── requirements.txt
├── runtime.txt            # Versión de Python para Render
└── Procfile               # web: gunicorn app:app
```

## Requisitos

- Python 3.12 o superior
- PostgreSQL accesible (local, Docker, Supabase, Render, etc.)
- Una cuenta de Gmail con [contraseña de aplicación](https://support.google.com/accounts/answer/185833) para el envío de correos

## Instalación

```bash
git clone https://github.com/jairprime/computer-labs-management
cd computer-labs-management
python -m venv venv
```

Activa el entorno virtual:

```bash
# macOS / Linux
source venv/bin/activate

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Windows (cmd)
venv\Scripts\activate.bat
```

> Si PowerShell bloquea el script, ejecuta una vez: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

Instala las dependencias:

```bash
pip install -r requirements.txt
```

## Configuración

Crea un archivo `.env` en la raíz del proyecto (ya está incluido en `.gitignore`):

```env
SECRET_KEY=una_clave_larga_y_aleatoria
DB_HOST=localhost
DB_PORT=5432
DB_NAME=nombre_de_tu_base
DB_USER=usuario
DB_PASSWORD=contraseña
EMAIL_USER=cuenta@gmail.com
EMAIL_PASS=contraseña_de_aplicacion
```

| Variable      | Descripción                                                                 |
| ------------- | --------------------------------------------------------------------------- |
| `SECRET_KEY`  | Firma las sesiones. Si falta, se genera una aleatoria en cada arranque.      |
| `DB_HOST`     | Host de PostgreSQL                                                           |
| `DB_PORT`     | Puerto de PostgreSQL (por defecto `5432`)                                    |
| `DB_NAME`     | Nombre de la base de datos                                                   |
| `DB_USER`     | Usuario de la base de datos                                                  |
| `DB_PASSWORD` | Contraseña de la base de datos                                               |
| `EMAIL_USER`  | Cuenta de Gmail que envía las notificaciones                                 |
| `EMAIL_PASS`  | Contraseña de aplicación de Gmail                                            |

### Base de datos

La aplicación no incluye migraciones, así que las tablas deben existir antes de arrancar. Este es un esquema de referencia inferido del código; el orden de las columnas de `control_laboratorios_sistemas` importa porque la exportación a Excel lo usa tal cual:

```sql
CREATE TABLE control_laboratorios_sistemas (
    id                        SERIAL PRIMARY KEY,
    fecha_registro            TIMESTAMP NOT NULL,
    identificador_laboratorio TEXT NOT NULL,
    nombre_docente            TEXT NOT NULL,
    correo_electronico        TEXT NOT NULL,
    programa                  TEXT NOT NULL,
    hora_ingreso              TIME NOT NULL,
    hora_salida               TIME NOT NULL,
    observacion               TEXT NOT NULL,
    respuesta_incidencia      TEXT
);

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL
);
```

### Primer administrador

El registro de usuarios (`/register`) solo está disponible con una sesión iniciada, así que el primer administrador se crea a mano. Genera el hash de la contraseña:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('tu_contraseña', method='pbkdf2:sha256', salt_length=8))"
```

E insértalo en la base:

```sql
INSERT INTO users (username, hashed_password) VALUES ('admin', '<hash_generado>');
```

## Desarrollo

```bash
python app.py
```

La aplicación queda disponible en <http://localhost:5000>.

## Producción

```bash
gunicorn app:app
```

### Despliegue en Render

1. Sube el código a un repositorio de GitHub.
2. Crea un nuevo **Web Service** en <https://dashboard.render.com> y conecta el repositorio.
3. Selecciona Python como entorno.
4. Configura las variables de entorno de la sección [Configuración](#configuración).
5. El `Procfile` ya define el comando de arranque (`web: gunicorn app:app`) y `runtime.txt` fija la versión de Python.

Render vuelve a desplegar automáticamente en cada push.

## Rutas

| Método    | Ruta                 | Descripción                                     | Auth |
| --------- | -------------------- | ----------------------------------------------- | ---- |
| GET       | `/`                  | Formulario público de incidentes                | No   |
| POST      | `/add_data`          | Registra un incidente                           | No   |
| GET, POST | `/login`             | Inicio de sesión (máx. 10 intentos/min por IP)  | No   |
| GET       | `/admin`             | Listado de incidentes                           | Sí   |
| GET       | `/edit/<id>`         | Formulario de edición / respuesta               | Sí   |
| POST      | `/update/<id>`       | Actualiza el incidente y notifica por correo    | Sí   |
| POST      | `/delete/<id>`       | Elimina un incidente                            | Sí   |
| GET       | `/users`             | Listado de administradores                      | Sí   |
| GET, POST | `/register`          | Crea un administrador                           | Sí   |
| GET, POST | `/restore/<id>`      | Restablece la contraseña de un administrador    | Sí   |
| POST      | `/deleteuser/<id>`   | Elimina un administrador                        | Sí   |
| GET       | `/reports`           | Página de reportes                              | Sí   |
| GET, POST | `/generate`          | Filtra incidentes por rango de fechas           | Sí   |
| GET       | `/download`          | Descarga el reporte filtrado en `.xlsx`         | Sí   |
| GET       | `/logout`            | Cierra la sesión                                | Sí   |

## Autor

- GitHub: <https://github.com/jairprime>
- Email: <jairenriquez1715@gmail.com>
