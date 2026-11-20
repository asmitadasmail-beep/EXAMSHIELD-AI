# EXAMSHIELD-AI
AI-Powered Online Exam Proctoring &amp; Cheating Detection System

## Teacher and administrator access

Administrative accounts are stored separately from student accounts. Passwords are hashed, and teachers can only access examinations assigned by a Super Admin.

1. Configure a strong `SECRET_KEY` environment variable before deployment. For HTTPS deployments, set `SESSION_COOKIE_SECURE=true`.
2. Create the first Super Admin interactively (the password is not supplied on the command line):

   ```powershell
   py -m flask --app app create-super-admin
   ```

3. Sign in at `/admin/login`. Use **Manage users** to create Teacher accounts and assign their examination access.

The initial schema setup creates the administrative account and permission tables if they do not already exist. There are no built-in administrative credentials.
