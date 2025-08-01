import gspread
from oauth2client.service_account import ServiceAccountCredentials


def setup_reporter(google_creds, sheet_id):
    scope = ['https://www.googleapis.com/auth/spreadsheets']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id).worksheet('Trades')


def log_trade(sheet, symbol: str, side: str, qty: float, entry: float, tp: float, sl: float, status: str):
    """Append a trade record to the Google Sheet."""
    sheet.append_row(
        [symbol, side, qty, entry, tp, sl, status],
        value_input_option="USER_ENTERED",
    )
