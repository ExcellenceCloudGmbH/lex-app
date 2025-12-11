from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
import markdown2
from xhtml2pdf import pisa
import io
from lex.audit_logging.models.calculation_log import CalculationLog

class DownloadMarkdownPdf(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, format=None):
        obj = CalculationLog.objects.filter(pk=pk).first()
        md_text = obj.calculation_log or ""

        # Convert Markdown → HTML (with table support)
        html_body = markdown2.markdown(
            md_text,
            extras=["tables", "fenced-code-blocks", "code-friendly"]
        )

        # Wrap in full HTML + CSS
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            table {{
              width: 100%;
              border-collapse: collapse;
              margin: 1em 0;
            }}
            th, td {{
              border: 1px solid #000;
              padding: 4px;
              text-align: left;
            }}
            th {{
              background-color: #f2f2f2;
            }}
          </style>
        </head>
        <body>
          {html_body}
        </body>
        </html>
        """

        result = io.BytesIO()
        pisa_status = pisa.CreatePDF(src=full_html, dest=result)

        if pisa_status.err:
            return HttpResponse("Error generating PDF", status=500)

        result.seek(0)
        resp = HttpResponse(result.read(), content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="document_{pk}.pdf"'
        return resp
