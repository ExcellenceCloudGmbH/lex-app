from django.http import JsonResponse
from django.views import View

from lex.audit_logging.handlers.lex_logger import LexLogger


class LexLoggerView(View):
    """
    A view to handle fetching logs. This assumes logs are stored in a 
    structured format like a database or a file you can read from.
    """

    def get(self, request, *args, **kwargs):
        # Here, you would fetch the logs from your storage (e.g., a database)
        # For simplicity, we are returning a static list of logs
        logs = [
            {'level': 'INFO', 'message': 'System started', 'timestamp': '2024-09-17 12:00:00'},
            {'level': 'WARNING', 'message': 'CPU usage high', 'timestamp': '2024-09-17 12:05:00'},
            {'level': 'ERROR', 'message': 'Application crashed', 'timestamp': '2024-09-17 12:10:00'},
        ]

        return JsonResponse(logs, safe=False)

    def post(self, request, *args, **kwargs):
        # Handle POST request to create a log (if needed)
        # Example of log creation or storage (typically, logging to file or database)
        message = request.POST.get('message', 'No message provided')
        log_level = request.POST.get('level', 'INFO')

        # Using the Python logging module to log
        logger = LexLogger()
        getattr(logger, log_level.lower(), logger.info)(message)

        return JsonResponse({'status': 'Log added successfully'}, status=201)
