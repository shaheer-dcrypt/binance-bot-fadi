import os
import json
import importlib

# 1) Configure which secret to fetch
_SECRET_NAME = os.getenv("CONFIG_SECRET", "Anita-BinanceBot")
_REGION = os.getenv("AWS_REGION", "ap-southeast-1")


def get_secrets():
    """
    Returns:
      (api_key: str,
       api_secret: str,
       google_creds: dict,
       sheet_id: str)
    """
    # 2) Try to load the AWS secret once
    try:
        boto3 = importlib.import_module("boto3")
        client = boto3.session.Session().client("secretsmanager", region_name=_REGION)
        blob = client.get_secret_value(SecretId=_SECRET_NAME)["SecretString"]
        data = json.loads(blob)
    except Exception:
        # if AWS fails (no creds, wrong name, etc.), fall back to env-only
        data = {}

    # 3) Extract each piece (fall back to os.environ where appropriate)
    api_key = data.get("BINANCE_API_KEY2") or os.getenv("BINANCE_API_KEY2")
    api_secret = data.get("BINANCE_API_SECRET2") or os.getenv("BINANCE_API_SECRET2")

    # Google creds are themselves JSON
    google_js = data.get("google_secrets") or os.getenv("google_secrets", "{}")
    google_creds = json.loads(google_js)

    # Sheet ID can live either in the secret or in SHEET_ID env var
    sheet_id = "1T-dkEwM7C9155X2Gz7ktiGx14hEbjpkH7Cje9JHiTFo"

    return api_key, api_secret, google_creds, sheet_id
