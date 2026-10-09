"""Forget the admin password: the next visit to the console sets a new one.
    docker exec inf-admin python -m reset"""
import main

st = main.load_state()
st.pop("password_hash", None)
st["session_secret"] = __import__("secrets").token_hex(32)     # logs every browser out
main.save_state(st)
print("admin password cleared; the next visit sets a new one")
