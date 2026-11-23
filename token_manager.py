from fyers_apiv3 import fyersModel
from dotenv import load_dotenv, set_key
from urllib.parse import urlparse, parse_qs
import os
import sys
import webbrowser

# ==========================================================
# LOAD ENVIRONMENT
# ==========================================================
load_dotenv()

CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI")

ENV_FILE = ".env"

# ==========================================================
# VALIDATE CONFIGURATION
# ==========================================================
missing = []

if not CLIENT_ID:
    missing.append("FYERS_CLIENT_ID")

if not SECRET_KEY:
    missing.append("FYERS_SECRET_KEY")

if not REDIRECT_URI:
    missing.append("FYERS_REDIRECT_URI")

if missing:
    print("\nMissing configuration in .env")

    for item in missing:
        print(f" - {item}")

    sys.exit()

# ==========================================================
# CREATE SESSION
# ==========================================================
session = fyersModel.SessionModel(
    client_id=CLIENT_ID,
    secret_key=SECRET_KEY,
    redirect_uri=REDIRECT_URI,
    response_type="code",
    grant_type="authorization_code"
)

# ==========================================================
# GENERATE LOGIN URL
# ==========================================================
login_url = session.generate_authcode()

print("=" * 60)
print("             FYERS TOKEN MANAGER")
print("=" * 60)

print("\nOpening browser...")
webbrowser.open(login_url)

print("\nIf browser doesn't open, use the URL below:\n")
print(login_url)

print("\nAfter login copy the COMPLETE redirect URL.")
print("Example:")
print("http://127.0.0.1/?s=ok&code=200&auth_code=xxxxx&state=None")

# ==========================================================
# READ REDIRECT URL
# ==========================================================
redirect_url = input("\nPaste Redirect URL:\n> ").strip()

# ==========================================================
# EXTRACT AUTH CODE
# ==========================================================
try:

    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)

    auth_code = params.get("auth_code", [None])[0]

    if not auth_code:
        raise Exception("auth_code not found")

    print("\n✓ Auth Code extracted.")

except Exception as ex:

    print("\nInvalid redirect URL.")
    print(ex)
    sys.exit()

# ==========================================================
# GENERATE ACCESS TOKEN
# ==========================================================
session.set_token(auth_code)

response = session.generate_token()

if response.get("s") != "ok":

    print("\nToken generation failed.")
    print(response)
    sys.exit()

access_token = response["access_token"]

# ==========================================================
# UPDATE .ENV
# ==========================================================
set_key(
    ENV_FILE,
    "FYERS_ACCESS_TOKEN",
    access_token
)

# ==========================================================
# SUCCESS
# ==========================================================
print("\n" + "=" * 60)
print("ACCESS TOKEN GENERATED SUCCESSFULLY")
print("=" * 60)

print("\n✓ .env updated successfully.")
print("\nAccess Token saved automatically.")
print("\nNow run:")
print("python main.py")
