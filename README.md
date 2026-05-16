# AI Attendance Pro

Production-ready Flask attendance dashboard with role-based authentication, browser-based camera capture, OpenCV face recognition, SQLite persistence, live monitoring, attendance snapshots, reports, avatar profiles, Gmail OTP password reset, websocket live updates, and a responsive SaaS-style UI.

## Stack

- Python 3.11
- Flask, Flask-SQLAlchemy, Flask-Sock, Gunicorn
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

- `Procfile`: `web: gunicorn --threads 4 --timeout 120 app:app`
- `runtime.txt`: Python 3.11
- `requirements.txt`: includes Gunicorn, websocket support, and headless OpenCV contrib support

Recommended Render settings:

- Environment: Python
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --threads 4 --timeout 120 app:app`
- Required env var: `SECRET_KEY`
- Optional SMTP env vars: `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`

Render free tier notes:

- The free filesystem is ephemeral. Runtime files such as `users.db`, snapshots, reports, and `trainer/trainer.yml` can disappear after redeploys or restarts.
- For demos, this lightweight SQLite setup is fine. For production, move persistence to Render PostgreSQL or another managed database and use object storage for snapshots/training artifacts.
- Camera access is browser-based through `navigator.mediaDevices.getUserMedia()`. The Render server receives sampled frames through API calls and pushes recognition state through websockets, so it does not need local webcam hardware.
- `opencv-contrib-python-headless` is used intentionally. It keeps LBPH recognition support while avoiding GUI OpenCV libraries that often fail on server hosts.

## Browser Camera Architecture

```text
Browser Camera
-> /api/camera/frame
-> OpenCV Face Recognition Engine
-> Attendance Database + CSV Reports
-> /ws/live Dashboard Updates
```

The browser owns camera permissions and live preview. The backend only receives compressed JPEG frames, recognizes faces, marks attendance, saves snapshots, and broadcasts dashboard updates. This is the cloud-compatible path for Render and commercial SaaS hosting.

## Face Workflow

1. Sign in as admin.
2. Add a user with full name, username, email, password, ID number, role, and optional avatar.
3. Click Capture to collect face samples from the browser camera.
4. The browser capture flow trains the model after enrollment, or you can run `python train.py`.
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
- Browser camera blocked: serve the app over HTTPS or localhost and allow camera permission in the browser.
- `trainer.yml missing`: train the model after collecting samples.
- SMTP errors: confirm Gmail app password and mail environment variables.

## Production Hardening

- Replace `SECRET_KEY` with a strong environment value.
- Use managed PostgreSQL for durable production data.
- Move snapshots and trained models to object storage for production deployments.
- Keep `.env`, databases, generated snapshots, and local virtual environments out of Git.
