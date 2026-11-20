import sys
import os

project_home = '/home/yourusername/EXAMSHIELD-AI-main'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ['SECRET_KEY'] = 'your-secret-key-here-change-this'

from app import app as application

if __name__ == "__main__":
    application.run()