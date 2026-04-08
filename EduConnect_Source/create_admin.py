import os
from app import app, db
from models import User
from werkzeug.security import generate_password_hash

# Drop existing database and recreate it
with app.app_context():
    # Drop all tables
    db.drop_all()
    db.create_all()

    # Create default admin user
    admin_user = User(
        username=input("Enter username for default admin user: "),
        email=input("Enter email for default admin user: "),
        password_hash=generate_password_hash(input("Enter password for default admin user: ")),
        is_admin=True,
        full_name=input("Enter full name for default admin user: ")
    )

    db.session.add(admin_user)
    db.session.commit()
    print("Database cleared and default admin user created.")