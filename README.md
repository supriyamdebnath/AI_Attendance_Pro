# AI Attendance Pro

Production-ready Flask attendance dashboard with role-based authentication, threaded OpenCV face recognition, SQLite persistence, live monitoring, attendance snapshots, reports, avatar profiles, Gmail OTP password reset, and a responsive SaaS-style UI.

## Stack

- Python 3.11
- Flask, Flask-SQLAlchemy, Gunicorn
- SQLite for the current lightweight deployment profile
- OpenCV Haarcascade + LBPH recognizer via `opencv-contrib-python-headless`
- Bootstrap 5, Chart.js, custom CSS/JavaScript
- CSV, Excel, and PDF exports
- Flask-Mail + python-dotenv for Gmail OTP password reset

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create `.env` from `.env.example`:

```text
SECRET_KEY=replace-with-a-long-random-secret
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your-gmail-address@gmail.com
MAIL_PASSWORD=your-gmail-app-password
MAIL_DEFAULT_SENDER=your-gmail-address@gmail.com
```

Use a Gmail app password for `MAIL_PASSWORD`. Do not commit real secrets.

4. Initialize or migrate the local database:

```bash
python database.py
```

5. Start the app:

```bash
python app.py
```

6. Open `http://127.0.0.1:5000`.

Default bootstrap admin:

- Username: `admin`
- Password: `Admin@12345`

Change the default password immediately in any real deployment.

## Render Deployment

This project is prepared for Render using:

- `Procfile`: `web: gunicorn app:app`
- `runtime.txt`: Python 3.11
- `requirements.txt`: includes Gunicorn and headless OpenCV contrib support

Recommended Render settings:

- Environment: Python
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Required env var: `SECRET_KEY`
- Optional SMTP env vars: `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`

Render free tier notes:

- The free filesystem is ephemeral. Runtime files such as `users.db`, snapshots, reports, and `trainer/trainer.yml` can disappear after redeploys or restarts.
- For demos, this lightweight SQLite setup is fine. For production, move persistence to Render PostgreSQL or another managed database and use object storage for snapshots/training artifacts.
- Webcam access is normally unavailable inside hosted Render containers. Live camera recognition works best on local machines, edge devices, or a browser-upload/websocket camera flow.
- `opencv-contrib-python-headless` is used intentionally. It keeps LBPH recognition support while avoiding GUI OpenCV libraries that often fail on server hosts.

## Face Workflow

1. Sign in as admin.
2. Add a user with full name, username, email, password, ID number, role, and optional avatar.
3. Click Capture to collect face samples from the webcam.
4. Train the model from the dashboard or run `python train.py`.
5. Open Live Monitoring.
6. Recognized users are marked present once per day in SQLite, `attendance.csv`, and `Attendance/attendance_<year>.csv`.
7. Snapshots are saved in `static/snapshots/` and linked to attendance rows.

## Runtime Files

These are intentionally ignored by Git:

- `venv/`, `.venv/`
- `__pycache__/`, `*.pyc`
- `.env`
- `users.db`
- `trainer/trainer.yml`
- generated snapshots in `static/snapshots/` and `uploads/snapshots/`
- generated reports in `Attendance/reports/`

The `.gitkeep` files preserve required runtime folders without committing generated data.

## Troubleshooting

- `opencv-contrib-python is required for LBPH training`: install from `requirements.txt`; the headless contrib package provides `cv2.face`.
- `No captured face images found`: capture user samples first.
- `Webcam unavailable`: close other camera apps locally; hosted Render containers typically cannot access a physical webcam.
- `trainer.yml missing`: train the model after collecting samples.
- SMTP errors: confirm Gmail app password and mail environment variables.

## Production Hardening

- Replace `SECRET_KEY` with a strong environment value.
- Use managed PostgreSQL for durable production data.
- Move snapshots and trained models to object storage for production deployments.
- Keep `.env`, databases, generated snapshots, and local virtual environments out of Git.
