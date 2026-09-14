"""Documentation screenshots that stay correct when the product moves.

Two capture front-ends, one annotation renderer:

    lex CLI      -> terminal.capture()   -\
                                           >-- Shot -> annotate.render() -> .svg
    Playwright   -> shot.json (PAC)      -/

The point of the split is the `Shot`: a capture records WHERE things are, not
just what they look like. A terminal capture resolves an anchor by matching the
output text; a browser capture resolves it from a CSS selector's bounding box.
Either way the arrow is placed by the renderer at coordinates the product
itself reported, so a moved button or a reworded line moves its arrow too
instead of leaving it pointing at empty space.
"""
