from dragon_quant.review_account.market import build_row


def build_live_row(history, quote, trade_date, bars, timestamp, phase):
    row = build_row(history, trade_date, quote.open_px, bars, timestamp,
                    execution_price=quote.price, limit_up=quote.limit_up, limit_down=quote.limit_down)
    row["phase"] = phase
    row["execution_timestamp"] = quote.timestamp
    return row
