# Control de Laboratorios de Sistemas

Aplicación web para reportar y dar seguimiento a las incidencias de las salas de cómputo de la Universidad CESMAG, construida con **Flask 3.1** y **PostgreSQL**. Cualquier docente reporta una incidencia desde un formulario público sin necesidad de cuenta, y el personal de Infraestructura TI la gestiona desde un panel protegido con login: responde, edita o elimina reportes, avisa por correo al docente cuando hay respuesta y exporta los registros a Excel.

## Stack

| Capa          | Tecnología                                                     |
| ------------- | -------------------------------------------------------------- |
| Backend       | Python 3.12, Flask 3.1, Gunicorn                               |
| Base de datos | PostgreSQL con `psycopg` 3 y `psycopg-pool` (sin ORM)          |
| Seguridad     | Flask-WTF (CSRF), Flask-Limiter, hashes de Werkzeug            |
| Reportes      | pandas, XlsxWriter                                             |
| Correo        | Gmail SMTP con contraseña de aplicación                        |
| UI            | Plantillas Jinja2, JavaScript, Bootstrap 4 (Bootswatch Materia) |
| Despliegue    | Render                                                         |

## Características

- **Formulario público de incidencias**: el docente elige el laboratorio, escribe su nombre, correo institucional, programa, hora de ingreso y salida y la observación. Se valida que todos los campos estén llenos, que el nombre solo tenga letras y espacios, que el correo termine en `@unicesmag.edu.co` y que la hora de salida sea posterior a la de ingreso.
- **Panel de administración** (requiere login): lista todos los reportes del más reciente al más antiguo y permite editarlos, responderlos o eliminarlos.
- **Aviso por correo**: al guardar una respuesta nueva o distinta a la anterior, se envía un correo al docente con el texto de la respuesta. El envío corre en un hilo aparte para no bloquear la petición.
- **Gestión de administradores**: crear cuentas, restablecer contraseñas (mínimo 8 caracteres) y eliminarlas. Solo es accesible con la sesión iniciada. Un administrador no puede eliminar su propia cuenta, así que siempre queda al menos uno, y quien es eliminado pierde el acceso de inmediato.
- **Reportes en Excel**: filtra las incidencias por rango de fechas y descarga el resultado como `.xlsx` con encabezados con formato y columnas ajustadas al contenido.
- **Seguridad**: protección CSRF en todos los formularios, límite de 10 intentos de login y 10 envíos del formulario por minuto por IP, contraseñas guardadas como hash `pbkdf2:sha256`, consultas parametrizadas, borrados solo por POST, exportación a Excel sin fórmulas (el texto se guarda como texto) y cookies de sesión `SameSite=Lax` y `Secure` en producción.

## Estructura

```
computer-labs-management/
├── app.py                 # Rutas y lógica de la aplicación
├── helpers.py             # Decorador login_required
├── sql/
│   └── schema.sql         # Tablas de la base de datos
├── templates/             # Vistas Jinja2
│   ├── layout.html        # Plantilla base
│   ├── index.html         # Formulario público de incidencias
│   ├── login.html
│   ├── admin.html         # Listado de reportes
│   ├── edit.html          # Editar y responder un reporte
│   ├── users.html         # Gestión de administradores
│   ├── register.html      # Crear administrador
│   ├── restore.html       # Restablecer contraseña
│   ├── reports.html       # Selección del rango de fechas
│   └── generate.html      # Vista previa y descarga del Excel
├── static/
│   ├── css/
│   ├── img/               # Logos
│   └── js/
├── requirements.txt
├── runtime.txt            # Versión de Python
└── Procfile               # Comando de arranque en Render
```

## Requisitos

- Python 3.12 o superior
- Una base de datos PostgreSQL accesible (local, Docker, Render, Supabase, etc.)
- Una cuenta de Gmail con verificación en dos pasos y una [contraseña de aplicación](https://myaccount.google.com/apppasswords) para enviar los correos

## Instalación

```bash
git clone https://github.com/jairprime/computer-labs-management.git
cd computer-labs-management
python3 -m venv venv
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

Instala las dependencias (Gunicorn ya está incluido):

```bash
pip install -r requirements.txt
```

## Configuración

Crea un archivo `.env` en la raíz del proyecto (ya está incluido en `.gitignore`):

```env
SECRET_KEY=una_clave_larga_y_aleatoria
DB_HOST=localhost
DB_PORT=5432
DB_NAME=nombre_de_la_base
DB_USER=usuario
DB_PASSWORD=contraseña
EMAIL_USER=cuenta@gmail.com
EMAIL_PASS=contraseña_de_aplicacion
```

| Variable      | Descripción                                                                                     |
| ------------- | ----------------------------------------------------------------------------------------------- |
| `SECRET_KEY`  | Firma las sesiones. Si falta se genera una aleatoria y las sesiones se pierden en cada reinicio |
| `DB_HOST`     | Servidor de PostgreSQL                                                                          |
| `DB_PORT`     | Puerto de PostgreSQL (normalmente `5432`)                                                       |
| `DB_NAME`     | Nombre de la base de datos                                                                      |
| `DB_USER`     | Usuario de la base de datos                                                                     |
| `DB_PASSWORD` | Contraseña de la base de datos                                                                  |
| `EMAIL_USER`  | Cuenta de Gmail que envía los avisos                                                            |
| `EMAIL_PASS`  | Contraseña de aplicación de Gmail, no la contraseña normal. Si falta, no se envían correos      |
| `RENDER`      | La define Render automáticamente; activa las cookies `Secure`. No la pongas en local            |

Genera la `SECRET_KEY` con:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Base de datos

La aplicación no incluye migraciones; las tablas están en `sql/schema.sql`. Créalas en una base vacía con:

```bash
psql -h <host> -p <puerto> -U <usuario> -d <base> -f sql/schema.sql
```

En Supabase también puedes pegar el contenido del archivo en el **SQL Editor** y ejecutarlo.

### Primer administrador

Crear administradores desde `/register` requiere tener la sesión iniciada, así que el primero se inserta directamente en la base. Genera el hash de la contraseña:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('tu_contraseña', method='pbkdf2:sha256', salt_length=8))"
```

E insértalo en PostgreSQL:

```sql
INSERT INTO users (username, hashed_password) VALUES ('admin', '<hash generado>');
```

## Desarrollo

```bash
python app.py
```

La aplicación queda disponible en <http://localhost:5000> (formulario) y <http://localhost:5000/login> (panel).

## Producción

```bash
gunicorn app:app
```

> El límite de intentos de login se guarda en memoria, así que solo es exacto con un único worker de Gunicorn, que es lo que usa `gunicorn app:app` por defecto.

### Despliegue en Render

1. En <https://dashboard.render.com> crea un **Web Service** y conecta este repositorio de GitHub.
2. Configura el servicio:

   | Opción          | Valor                             |
   | --------------- | --------------------------------- |
   | Runtime         | Python                            |
   | Build Command   | `pip install -r requirements.txt` |
   | Start Command   | `gunicorn app:app` (el mismo del `Procfile`) |
   | Variables       | `SECRET_KEY`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `EMAIL_USER`, `EMAIL_PASS` |

3. Crea las tablas y el primer administrador en la base de datos de producción (ver [Base de datos](#base-de-datos)).
4. Pulsa **Create Web Service**. Cada `git push` a `main` vuelve a desplegar automáticamente.

## Rutas

| Método    | Ruta               | Descripción                                             | Auth |
| --------- | ------------------ | ------------------------------------------------------- | ---- |
| GET       | `/`                | Formulario público de incidencias                       | No   |
| POST      | `/add_data`        | Guarda una incidencia (máx. 10 envíos/min por IP)       | No   |
| GET, POST | `/login`           | Inicio de sesión (máx. 10 intentos/min por IP)          | No   |
| GET       | `/admin`           | Listado de reportes                                     | Sí   |
| GET       | `/edit/<id>`       | Formulario para editar o responder un reporte           | Sí   |
| POST      | `/update/<id>`     | Guarda los cambios y, si hay respuesta nueva, avisa por correo | Sí   |
| POST      | `/delete/<id>`     | Elimina un reporte                                      | Sí   |
| GET       | `/users`           | Listado de administradores                              | Sí   |
| GET, POST | `/register`        | Crea un administrador                                   | Sí   |
| GET, POST | `/restore/<id>`    | Restablece la contraseña de un administrador            | Sí   |
| POST      | `/deleteuser/<id>` | Elimina un administrador                                | Sí   |
| GET       | `/reports`         | Selección del rango de fechas                           | Sí   |
| GET, POST | `/generate`        | Vista previa de los reportes filtrados                  | Sí   |
| GET       | `/download`        | Descarga el Excel del último filtro aplicado            | Sí   |
| GET       | `/logout`          | Cierra la sesión                                        | Sí   |

## Autor

- GitHub: <https://github.com/jairprime>
- Email: <jairenriquez1715@gmail.com>
