# custom_logger.py
import ast
import logging
import time
import uuid

import mistune
import pandas as pd
from django.conf import settings

from lex.utilities.decorators.singleton import LexSingleton
from lex.audit_logging.models.calculation_log import CalculationLog


class LexLogLevel:
    VERBOSE = 5
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


@LexSingleton
class LexLogger:
    def __init__(self):
        self.content = []

    def add_raw_markdown(self, markdown: str):
        for line in markdown.splitlines():
            self.content.append(line)
        self.content.append("")

        return self

    def add_text(self, text: str):
        """Adds plain text (as a paragraph)."""
        self.content.append(text)
        self.content.append("")  # empty line for separation
        return self

    def add_heading(self, heading: str, level: int = 1):
        """Adds a heading. Level can be from 1 to 6."""
        level = max(1, min(level, 6))  # Ensure heading level is between 1 and 6
        self.content.append(f"{'#' * level} {heading}")
        self.content.append("")
        return self

    def add_list(self, items: list, ordered: bool = False):
        """Adds a list (ordered or unordered)."""
        if ordered:
            for i, item in enumerate(items, start=1):
                self.content.append(f"{i}. {item}")
        else:
            for item in items:
                self.content.append(f"- {item}")
        self.content.append("")
        return self

    def add_quote(self, quote: str):
        """Adds a blockquote."""
        self.content.append(f"> {quote}")
        self.content.append("")
        return self

    def add_code(self, code: str, language: str = ""):
        """Adds a fenced code block. Optionally specify a language for syntax highlighting."""
        self.content.append(f"```{language}")
        self.content.append(code)
        self.content.append("```")
        self.content.append("")
        return self

    def add_link(self, text: str, url: str):
        """Adds a hyperlink."""
        self.content.append(f"[{text}]({url})")
        self.content.append("")
        return self

    def add_image(self, alt_text: str, url: str):
        """Adds an image."""
        self.content.append(f"![{alt_text}]({url})")
        self.content.append("")
        return self

    def add_horizontal_rule(self):
        """Adds a horizontal rule."""
        self.content.append("---")
        self.content.append("")
        return self

    def add_table(self, headers: list, rows: list):
        """Adds a markdown table.

        Parameters:
            headers: A list of strings for the header row.
            rows: A list of lists, where each inner list represents a row in the table.
        """
        # Build header row
        header_row = "| " + " | ".join(headers) + " |"
        self.content.append(header_row)
        # Build separator row required by markdown
        separator = "| " + " | ".join(["---"] * len(headers)) + " |"
        self.content.append(separator)
        # Add each row
        for row in rows:
            row_line = "| " + " | ".join(row) + " |"
            self.content.append(row_line)
        self.content.append("")
        return self

    def add_dataframe(self, df: pd.DataFrame):
        """Adds a pandas DataFrame rendered as a Markdown table.

        This method uses DataFrame.to_markdown() if available.
        """
        try:
            markdown_table = df.to_markdown(index=False)
        except Exception:
            # Fall back to using tabulate if to_markdown fails
            from tabulate import tabulate
            markdown_table = tabulate(df, headers='keys', tablefmt='pipe', showindex=False)

        self.content.append(markdown_table)
        self.content.append("")
        return self

    def log(self):
        final_output = "\n".join(self.content)
        CalculationLog.log(final_output)
        self.content = []
        return self