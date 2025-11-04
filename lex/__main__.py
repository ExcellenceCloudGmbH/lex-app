# lex/__main__.py  (unchanged trampoline)
import sys

def main() -> None:
    from lex.bin.lex import main as _main
    sys.exit(_main())

if __name__ == "__main__":
    main()
