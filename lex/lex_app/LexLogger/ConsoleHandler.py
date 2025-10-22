import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

class ConsoleHandler(logging.Handler):
    """
    A logging.Handler which, on each record, sends the formatted
    message to the appropriate WebSocket group (calculation_record).
    """
    def emit(self, record: logging.LogRecord):
        try:
            # format the message (use whatever Formatter you like)
            formatted = self.format(record)

            # pull our extra fields out of the record
            calc_record = record.__dict__.get("calculation_record")
            calc_id     = record.__dict__.get("calculationId")

            if not calc_record:
                # nothing to do if no group name
                return

            payload = {
                "type": "calculation_log_real_time",
                "payload": formatted,
            }

            print(formatted)
        except Exception:
            self.handleError(record)