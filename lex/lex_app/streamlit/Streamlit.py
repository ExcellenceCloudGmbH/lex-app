import os

from lex.core.models.html_report import HTMLReport


class Streamlit(HTMLReport):

    def get_html(self, user):
        return f"""<iframe
              src="{os.getenv("STREAMLIT_URL", "http://localhost:8501")}/?embed=true"
              style="width:100%;border:none;height:100%"
            ></iframe>"""
