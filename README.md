# AI Attendance Pro

Production-oriented Flask attendance dashboard with role-based authentication, threaded OpenCV face recognition, SQLite persistence, live monitoring, attendance snapshots, reports, avatar profiles, Gmail OTP password reset, and a responsive dark blue SaaS UI.

## Stack

- Python 3.11
- Flask + Flask-SQLAlchemy
- SQLite
- OpenCV Haarcascade + LBPH recognizer
- Bootstrap 5, Chart.js, custom CSS/JavaScript
- CSV and Excel exports
- Flask-Mail + python-dotenv for secure reset emails

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

For the new SMTP reset flow specifically, make sure these packages are installed:

```bash
pip install Flask-Mail python-dotenv
```

3. Create a `.env` file from `.env.example` and set:

```text
SECRET_KEY=replace-with-a-long-random-secret
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your-gmail-address@gmail.com
MAIL_PASSWORD=your-gmail-app-password
MAIL_DEFAULT_SENDER=your-gmail-address@gmail.com
```

Use a Gmail app password for `MAIL_PASSWORD`. Do not put real secrets in source control.

4. Initialize the database:

```bash
python database.py
```

5. Start the web app:

```bash
python app.py
```

6. Open the local app in a browser at `http://127.0.0.1:5000`.

Default bootstrap admin:

- Username: `admin`
- Password: `Admin@12345`

Change that password immediately in a real deployment.

## Face Workflow

1. Sign in as admin.
2. Open **Users** and register a person with full name, username, email, password, ID number, role, and optional avatar.
3. Click **Capture** to collect face samples from the webcam.
4. Return to the admin dashboard and click **Train model**.
5. Open **Live Monitoring** or use the dashboard camera controls.
6. Recognized users are marked present once per day in SQLite, `attendance.csv`, and `Attendance/attendance_<year>.csv`.
7. Snapshots are saved in `static/snapshots/` and linked to attendance rows.

You can also train from the terminal:

```bash
python train.py
```

## Files Generated at Runtime

- `users.db`
- `trainer/trainer.yml`
- attendance snapshots in `static/snapshots/`
- additional user avatars in `static/images/avatars/`
- report exports generated on demand

## Password Reset / SMTP

The reset flow uses Gmail OTP verification and is SMTP-ready. Configure:

```text
SECRET_KEY=
MAIL_USERNAME=
MAIL_PASSWORD=
```

The app defaults to Gmail SMTP at `smtp.gmail.com:587` with TLS. Without SMTP configuration, OTP values are logged by Flask so the flow remains testable locally. Invalid credentials or connectivity errors are shown as professional alerts.

## Reports

Admins can:

- view attendance history
- export CSV
- export Excel `.xlsx`
- export PDF
- search users
- inspect attendance trends from dashboard charts
- preview attendance snapshots
- review role-wise attendance counts

## Troubleshooting

- `opencv-contrib-python is required for LBPH training`
  Install the dependency from `requirements.txt`.
- `No captured face images found`
  Capture user samples first.
- `Webcam unavailable`
  Close other apps using the camera and retry.
- `trainer.yml` missing
  Train the model after collecting samples.
- Empty recognition feed
  Confirm webcam access, then restart the camera controls.
- Duplicate user error
  Use a unique username, email, and ID number.

## Deployment Notes

- Replace `SECRET_KEY`.
- Configure SMTP securely.
- Run behind a real WSGI server and reverse proxy.
- Store environment values outside source control.
- For mobile apps, the backend routes and exports are ready to be wrapped behind token-based APIs later without changing the core data model.
