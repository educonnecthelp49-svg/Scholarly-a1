import os
from dotenv import load_dotenv
import logging
from datetime import timedelta
from flask import Flask
from extensions import db
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_cors import CORS

logging.basicConfig(level=logging.DEBUG)

load_dotenv()

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key")
app.config['SESSION_COOKIE_SECURE'] = False  # Allow HTTP for development
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure the database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("SQLALCHEMY_DATABASE_URI", "sqlite:///school_social.db")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# File upload configuration
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Initialize the app with the extension
db.init_app(app)

# Initialize with CORS
CORS(app=app)


with app.app_context():
    # Import models to ensure tables are created
    import models as models
    db.create_all()

# Import routes after app initialization
from routes import *

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
