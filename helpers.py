# Import libraries
from flask import redirect, session
from functools import wraps

# Decorator that protects a route behind login
def login_required(f):
    # Preserves original function information
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if there is a user ID in the session
        if session.get("user_id") is None:
            # If there is no user ID, redirect the user to the login page
            return redirect("/login")

        # If the user is authenticated execute the original function
        return f(*args, **kwargs)

    return decorated_function