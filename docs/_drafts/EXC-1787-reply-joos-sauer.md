# Reply — EXC-1787 (draft, local only — not committed)

**To:** Joos Sauer (via Melih Sunbul)
**From:** Hazem Sahbani

---

Hi Joos,

Thanks for the report. We've fixed this in the framework — the next release cleans
up the local startup logs and adds a switch for framework debug output.

**What changes for you**

- The `InsecureRequestWarning` (and other startup warnings) are now hidden by
  default. Nothing to do on your side — your console will just be quieter.
- To see the framework's own debug logs without the noise from third-party
  libraries, set `LEX_LOG_LEVEL=DEBUG` in your `.env`.

**The available switches**

| Variable | Default | Effect |
| --- | --- | --- |
| `LEX_LOG_LEVEL` | `INFO` | `DEBUG` shows Lex framework debug logs only |
| `LEX_SUPPRESS_WARNINGS` | `True` | Hides Python startup warnings; set `False` to bring them back |
| `LEX_SUPPRESS_INSECURE_WARNING` | `True` | Hides the urllib3 TLS warning; set `False` to bring it back |

These are read at startup, so restart your `lex start` process after changing `.env`.

Note: this only quiets the warning message — it doesn't change how the connection
is made, so there's no security impact.

Let me know if anything still looks noisy.

Best regards,
Hazem Sahbani
